from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .core import PipelineError, load_json, normalize_repo_relative_path, sha256_payload
from .knowledge_assets import load_knowledge_assets, resolve_knowledge_asset_paths
from .retrieval_policy import enforce_actionability_policy, lane_metadata_for_ranked, normalize_task_archetype
from .retrieval_bundle_roles import classify_bundle_role, select_bundle_candidates, summarize_bundle
from .retrieval_diagnostics import compute_bundle_diagnostics, compute_rank_diagnostics
from .v2_foundation import (
    ensure_context_bundle_foundation,
    ensure_intake_assessment,
    ensure_patch_plan_foundation,
    ensure_ranked_context_foundation,
    ensure_search_plan_foundation,
    ensure_task_profile_foundation,
)
from .planning_ontology import classify_file_role, compatible_candidate_role, derive_bundle_role, evaluate_bundle_composition, file_role_weight
from .verification_policy import build_stage11_contract_section, build_stage12_contract_section, build_stage13_contract_section

IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
IMPORT_RE = re.compile(r"^(from\s+\S+\s+import\s+.+|import\s+.+)$")
FUNC_RE = re.compile(r"^(\s*)(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")

MAILBOX_WRITEBACK_MAILBOX_TERMS = (
    "mailbox",
    "mailbox claim",
    "claim delivery",
    "request mailbox",
    "delivery semantics",
)
MAILBOX_WRITEBACK_LIFECYCLE_TERMS = (
    "job lifecycle",
    "lifecycle",
    "detached job",
    "long-running job",
    "terminal state",
    "heartbeat",
    "timeout",
)
MAILBOX_WRITEBACK_WRITEBACK_TERMS = (
    "writeback",
    "canonical writeback",
    "task truth",
    "canonical task truth",
    "truth contract",
    "truth reconciliation",
)
MAILBOX_WRITEBACK_IDEMPOTENT_TERMS = (
    "idempotent",
    "idempotency",
    "duplicate suppression",
    "duplicate",
    "replay",
    "no_auto_retry",
    "terminal_no_auto_retry",
)


GLOBAL_REPLACE_PATTERNS = [
    re.compile(r'''replace\s+all\s+references\s+to\s+["']?(?P<old>[^"']+?)["']?\s+with\s+(?:the\s+word\s+)?["']?(?P<new>[^"'
]+?)["']?(?:[.?!]|$)''', re.IGNORECASE),
    re.compile(r'''replace\s+all\s+occurrences\s+of\s+["']?(?P<old>[^"'
]+?)["']?\s+with\s+["']?(?P<new>[^"'
]+?)["']?(?:[.?!]|$)''', re.IGNORECASE),
    re.compile(r'''rename\s+["']?(?P<old>[^"'
]+?)["']?\s+to\s+["']?(?P<new>[^"'
]+?)["']?\s+everywhere(?:[.?!]|$)''', re.IGNORECASE),
]


def detect_global_replace(job_contract: Mapping[str, Any]) -> Optional[Dict[str, str]]:
    text = " ".join([
        str(job_contract.get("title") or ""),
        str(job_contract.get("description") or ""),
        str(job_contract.get("objective") or ""),
    ]).strip()
    for pattern in GLOBAL_REPLACE_PATTERNS:
        m = pattern.search(text)
        if m:
            old = str(m.group('old') or '').strip().strip("\"'")
            new = str(m.group('new') or '').strip().strip("\"'")
            if old and new and old != new:
                return {"old": old, "new": new, "mode": "global_replace"}
    return None


def detect_mailbox_writeback_seam_focus_from_text(text: object, *, title_text: object = "") -> bool:
    combined = " ".join([str(title_text or ""), str(text or "")]).strip().lower()
    if not combined:
        return False
    mailbox_like = any(term in combined for term in MAILBOX_WRITEBACK_MAILBOX_TERMS)
    lifecycle_like = any(term in combined for term in MAILBOX_WRITEBACK_LIFECYCLE_TERMS)
    writeback_like = any(term in combined for term in MAILBOX_WRITEBACK_WRITEBACK_TERMS)
    idempotent_like = any(term in combined for term in MAILBOX_WRITEBACK_IDEMPOTENT_TERMS)
    return bool(mailbox_like and writeback_like and (lifecycle_like or idempotent_like))


def detect_mailbox_writeback_seam_focus(
    job_contract: Mapping[str, Any],
    *,
    task_profile: Optional[Mapping[str, Any]] = None,
    search_plan: Optional[Mapping[str, Any]] = None,
    query_terms: Optional[Iterable[str]] = None,
) -> bool:
    secondary_tags = {
        str(item or "").strip().lower()
        for item in list((task_profile or {}).get("secondary_tags") or [])
        if str(item or "").strip()
    }
    if "mailbox_writeback_seam" in secondary_tags:
        return True
    text_parts = [
        str(job_contract.get("title") or ""),
        str(job_contract.get("description") or ""),
        str(job_contract.get("objective") or ""),
        str(job_contract.get("issue_context", {}).get("user_notes") or ""),
        str((search_plan or {}).get("semantic_query_text") or ""),
        " ".join(str(item or "") for item in list((search_plan or {}).get("lexical_queries") or [])[:16]),
        " ".join(str(item or "") for item in list((search_plan or {}).get("subsystem_hypotheses") or [])[:8]),
        " ".join(str(item or "") for item in list(query_terms or [])),
    ]
    title_text = str(job_contract.get("title") or "")
    return detect_mailbox_writeback_seam_focus_from_text(" ".join(text_parts), title_text=title_text)


def _objective_requests_test_edits(job_contract: Mapping[str, Any]) -> bool:
    text = " ".join(
        [
            str(job_contract.get("title") or ""),
            str(job_contract.get("description") or ""),
            str(job_contract.get("objective") or ""),
        ]
    ).strip().lower()
    if not text:
        return False
    mentions_tests = any(token in text for token in [" test", " tests", "test ", "tests/", "test_", "testing"])
    requests_change = any(token in text for token in ["add ", "update ", "modify ", "change ", "create ", "write "])
    soft_conditional = any(phrase in text for phrase in ["if needed", "if necessary", "if required", "where needed"])
    strong_specificity = any(
        phrase in text
        for phrase in [
            "to prove",
            "to cover",
            "covering",
            "add tests for",
            "update tests for",
            "add or update tests",
            "exact tests",
            "new test",
            "new tests",
        ]
    )
    if soft_conditional and not strong_specificity:
        return False
    return bool(mentions_tests and requests_change)


def _promote_test_modify_target(entry: Mapping[str, Any]) -> Dict[str, Any]:
    path = str(entry.get("path") or "")
    symbols = [str(item) for item in list(entry.get("symbols") or []) if str(item).strip()]
    return {
        "path": path,
        "role": str(entry.get("bundle_role") or entry.get("role") or "validation_target"),
        "candidate_role": str(entry.get("candidate_role") or "test_file"),
        "ownership_role": str(entry.get("ownership_role") or ""),
        "change_type": "update_tests",
        "edit_intent": "explicit test update required by prompt",
        "symbols_to_edit": symbols,
        "intended_modifications": [
            "add or update focused regression coverage required explicitly by the prompt",
        ],
        "rationale": [
            "prompt explicitly requested test updates",
        ],
        "acceptance_criteria_coverage": [],
        "risk_level": "low",
    }


def _looks_like_concrete_test_command(command: object) -> bool:
    text = str(command or "").strip().lower()
    if not text:
        return False
    return bool(
        text.startswith("pytest ")
        or text == "pytest"
        or text.startswith("python -m pytest ")
        or text == "python -m pytest"
        or text.startswith("python3 -m pytest ")
        or text == "python3 -m pytest"
        or text.startswith("python -m unittest ")
        or text == "python -m unittest"
        or text.startswith("python3 -m unittest ")
        or text == "python3 -m unittest"
    )


def _is_runtime_test_path(path: object) -> bool:
    rel_path = str(path or "").strip().replace("\\", "/")
    if not rel_path:
        return False
    name = Path(rel_path).name
    if rel_path.startswith("tests/"):
        return True
    if name.startswith("test_") and rel_path.endswith(".py"):
        return True
    if rel_path.endswith("_test.py"):
        return True
    return False


def _synthesized_required_test_commands(*, validation_target_files: Sequence[object], modify_files: Sequence[Mapping[str, Any]]) -> List[str]:
    candidate_paths = _dedupe_strings(
        [item for item in validation_target_files if _is_runtime_test_path(item)]
        + [
            entry.get("path")
            for entry in list(modify_files or [])
            if _is_test_modify_candidate(entry) and _is_runtime_test_path(entry.get("path"))
        ]
    )
    if not candidate_paths:
        return []
    return [f"python3 -m pytest -q {' '.join(candidate_paths)}"]


def _resolve_required_test_commands(
    *,
    patch_plan_required_tests: Sequence[object],
    validation_target_files: Sequence[object],
    modify_files: Sequence[Mapping[str, Any]],
) -> List[str]:
    concrete = _dedupe_strings(item for item in patch_plan_required_tests if _looks_like_concrete_test_command(item))
    if concrete:
        return concrete
    return _synthesized_required_test_commands(
        validation_target_files=validation_target_files,
        modify_files=modify_files,
    )



TASK_FAMILY_PATTERNS = {
    "mobile_android_ui": [
        r"android", r"mobile app", r"apk", r"gradle", r"admin page", r"terminal emulator", r"ui test", r"device"
    ],
    "environment_permissions": [
        r"bashrc", r"java_home", r"socket", r"sandbox", r"permission", r"wsl", r"env var", r"java"
    ],
    "workflow_recovery": [
        r"workflow", r"resume", r"cancelled", r"orphaned", r"heartbeat", r"interrupt", r"recovery"
    ],
    "multi_agent_orchestration": [
        r"agent", r"orchestrator", r"swarm", r"parallel", r"worktree", r"merge", r"builder", r"verifier"
    ],
    "config_tuning": [
        r"timeout", r"idle timeout", r"policy", r"runtime_policy", r"default"
    ],
    "testing_verification": [
        r"test failed", r"unit test", r"verification", r"proof", r"assert", r"host verification"
    ],
    "webui_admin": [
        r"web app", r"admin", r"copy all button", r"deploy", r"terminal"
    ],
    "scheduler_hardening": [
        r"scheduler", r"scheduled", r"watchdog", r"queue", r"failure modes", r"pipeline pause", r"job cancel"
    ],
    "tool_spec_implementation": [
        r"stage 5", r"stage 6", r"stage 7", r"step 5", r"step 6", r"step 7", r"context bundle",
        r"patch plan", r"execution contract", r"planning\.py", r"search plan", r"rank context",
        r"semantic handler", r"semantic retrieval", r"embedding", r"projflow", r"pipeline runner",
    ],
    "documentation_contracts": [
        r"\bcontract\b", r"\bprompt\b", r"\bspec\b", r"\bbootstrap\b", r"workflow front-end", r"\bmvp\b"
    ],
    "rename_replace": [
        r"replace all references", r"replace all occurrences", r"rename", r"everywhere"
    ],
    "deterministic_patch_helper": [
        r"deterministic patch", r"refactor helper", r"patch hash", r"canonical replacements", r"recipe_id"
    ],
    "logs_artifacts_inspection": [
        r"logs", r"artifact", r"job_state", r"pipeline_run_summary", r"review artifact"
    ],
    "lab_assistant_memory": [
        r"lab assistant", r"research memory", r"cross-project", r"provenance", r"freshness", r"truth ownership",
        r"task register", r"execution reconciliation", r"transcript", r"ledger", r"correction history", r"memory-backed"
    ],
    "assistant_truth_and_identity": [
        r"assistant identity", r"source[- ]of[- ]truth", r"runtime state", r"ledger truth", r"transcript precedence",
        r"append-only event log", r"write arbitration", r"write generation", r"recovery marker", r"blocked state", r"schema version"
    ],
    "orchestration_checkpointing": [
        r"checkpoint", r"checkpointing", r"idempotent", r"duplicate execution", r"duplicate suppression",
        r"turn execution", r"turn checkpoint", r"crash window", r"resume after restart", r"replay safety",
        r"builder turn", r"verifier turn", r"started before", r"completed before"
    ],
    "ui_truth_projection": [
        r"ui projection", r"projection", r"snapshot", r"stale", r"degraded", r"unreachable",
        r"reconnecting", r"operator view", r"cached truth", r"display vs truth", r"backend reachability"
    ],
    "retrieval_ranking_contract": [
        r"retrieval ranking", r"evidence-first", r"evidence first", r"freshness", r"applicability",
        r"answer assembly", r"missing evidence", r"cross-project retrieval", r"memory-backed answer",
        r"rank direct evidence", r"inspectable source"
    ],
}

SCHEDULER_SURFACE_HINTS = [
    "infra/controlplane/scheduled_jobs.py",
    "infra/controlplane/scheduler_retry_manager.py",
    "infra/bin/scheduler_",
    "scheduler_watchdog",
    "scheduler_pipeline_healer",
    "scheduler_recovery_assistant",
    "scheduler_schedule_job",
    "scheduler_start_job",
    "scheduler_verify_job",
    "scheduler_live_watch",
    "scheduler_postpone_active_job",
    "scheduler_enable_disable_jobs",
    "scheduler_change_job_model",
]

TOOL_SPEC_SURFACE_HINTS = [
    "infra/pipeline/planning.py",
    "infra/pipeline/core.py",
    "infra/pipeline/schemas.py",
    "infra/pipeline/semantic.py",
    "infra/pipeline/run_summary.py",
    "infra/pipeline/stages/search_plan.py",
    "infra/pipeline/stages/repo_retriever.py",
    "infra/bin/pipeline_run.py",
    "infra/bin/pipeline_review.py",
    "infra/bin/pipeline_decide.py",
    "infra/bin/go",
    "infra/bin/projflow.py",
]

READ_ONLY_ROLES = {"reference_context", "dependency_context"}
EDITABLE_ROLES = {"primary_target", "secondary_target", "config_target", "paired_test", "edit_target", "integration_target", "validation_target"}
NON_DOC_EXTENSIONS = (".md", ".txt", ".rst")

RECOVERY_FOCUS_FLAGS = {"recovery_integrity", "stale_state_handling", "claim_integrity", "ownership_control", "mutation_boundary", "idempotency"}
OPERATOR_FOCUS_FLAGS = {"operator_safety", "manual_override_safety", "operator_auditability"}
VALIDATION_FOCUS_FLAGS = {"operator_safety", "validation_integrity"}
RECOVERY_FOCUS_TERMS = ("recovery", "repair", "reconcile", "healer", "watchdog", "orphan", "stale claim", "checkpoint", "resume", "heartbeat", "retry", "manual", "operator", "human failure")


MEMORY_FOCUS_FAMILIES = {"lab_assistant_memory"}
MEMORY_FOCUS_TERMS = ("memory", "knowledge", "provenance", "freshness", "transcript", "ledger", "truth ownership", "task register", "cross-project", "correction", "contradiction", "writeback")
MEMORY_OWNER_COVERS = {"truth_boundary", "reconciliation_path", "task_register_flow", "transcript_flow", "ledger_evidence", "memory_retrieval", "writeback_boundary", "memory_ownership"}
MEMORY_OWNER_PATH_MARKERS = ("server_lab_assistant_", "server_ledger_", "/ledger/")
MEMORY_HUB_BASENAMES = {"server_routes_and_main.py", "scheduled_jobs.py", "search_plan.py", "planning.py"}
TRUTH_CONTRACT_FAMILIES = {"assistant_truth_and_identity"}
TRUTH_CONTRACT_OWNER_SUBSYSTEMS = {"assistant_runtime_state", "runtime_state", "truth_registry", "state_persistence", "recovery_markers"}
TRUTH_CONTRACT_BOUNDARY_TOKENS = {"task_register", "writeback", "delegation", "bootstrap", "recovery", "host"}
TRUTH_CONTRACT_PROJECTION_TOKENS = {"snapshot", "projection", "status", "turns", "transcript"}
TRUTH_CONTRACT_ROUTE_TOKENS = {"route", "router", "routes_and_main"}
TRUTH_CONTRACT_DOC_TOKENS = {"contract", "identity", "truth", "state"}
ORCHESTRATION_CHECKPOINT_FAMILIES = {"orchestration_checkpointing"}
ORCHESTRATION_OWNER_SUBSYSTEMS = {"orchestration", "execution_control", "workflow_runtime", "state_persistence", "recovery_mechanisms", "validation"}
ORCHESTRATION_OWNER_TOKENS = {"checkpoint", "workflow", "orchestrator", "turn", "execution", "builder", "verifier", "resume", "replay"}
ORCHESTRATION_BOUNDARY_TOKENS = {"host", "route", "router", "server_agents", "server_routes", "static", "ui"}
UI_TRUTH_PROJECTION_FAMILIES = {"ui_truth_projection"}
UI_PROJECTION_OWNER_TOKENS = {"snapshot", "projection", "status", "health", "reachability", "degraded", "unreachable", "reconnecting"}
UI_PROJECTION_BOUNDARY_TOKENS = {"delegation", "binding", "command", "transport", "replace", "rejoin", "scheduler", "task_register"}
RETRIEVAL_RANKING_FAMILIES = {"retrieval_ranking_contract"}
RETRIEVAL_RANKING_OWNER_TOKENS = {"planning", "search_plan", "retrieval", "rank", "ranking", "semantic", "bundle", "context", "answer", "assembler", "evidence", "freshness", "policy", "applicability"}
RETRIEVAL_RANKING_HUB_TOKENS = {"task_register", "operator_snapshot", "mailbox", "server_routes", "server_agents", "apply_patch", "job_registry", "execution_reconciliation", "execution_truth_ownership", "writeback", "ledger_manager", "turns"}
RETRIEVAL_RANKING_TEST_PATH_HINTS = {
    "tests/pipeline/test_planning_flow.py",
    "tests/pipeline/test_stage3_search_plan_engine.py",
}
SCHEDULER_OWNER_TOKENS = {"scheduled_jobs", "scheduler", "watchdog", "healer", "claim", "activation", "retry"}
NARROW_TASK_NOUN_MARKERS = {
    "button", "buttons", "checkbox", "endpoint", "route", "field", "page", "panel", "tab", "width",
    "layout", "spacing", "validation", "rule", "dialog", "modal", "tooltip", "label", "column",
}
NARROW_TASK_VERB_MARKERS = {
    "fix", "resize", "resized", "adjust", "align", "toggle", "show", "hide", "limit", "clamp",
    "rename", "validate", "disable", "enable", "tighten", "patch",
}
CORE_HUB_PATH_MARKERS = {
    "scheduled_jobs.py": "scheduler_hub",
    "server_routes_and_main.py": "webui_router_hub",
    "server_agents_orchestrator.py": "webui_orchestrator_hub",
    "planning.py": "pipeline_planner_hub",
    "search_plan.py": "pipeline_stage_hub",
    "agent_benchmark.py": "benchmark_harness",
    "test_planning_flow.py": "broad_test_harness",
    "test_mobile_chat_agents.py": "broad_test_harness",
    "test_scheduled_jobs.py": "broad_test_harness",
    "test_webui_agents_orchestration.py": "broad_test_harness",
}
CORE_HUB_GENERIC_TOKENS = {"agent", "agents", "benchmark", "jobs", "main", "pipeline", "plan", "routes", "scheduled", "search", "server", "test", "webui"}
OWNER_SIGNAL_COVERS = {"state_mutation", "validation_path", "recovery_responsibility", "truth_boundary", "reconciliation_path", "task_register_flow", "transcript_flow", "ledger_evidence", "memory_retrieval", "writeback_boundary", "retrieval_owner"}
HUB_SPAN_COVERS = {"recovery_path", "recovery_responsibility", "ownership_control", "state_mutation", "operator_flow", "validation_path", "dispatch_path", "retrieval_scoring", "retrieval_search", "retrieval_answer", "retrieval_rules"}
LOCAL_KNOWLEDGE_REPO_ROOT = Path(__file__).resolve().parents[2]
PLANNING_ASSET_NAMES = ["subsystem_map", "bundle_coverage_rules", "passive_relevance_patterns", "scoring_weights"]
FAILURE_CATEGORY_COVER_MAP: Dict[str, Set[str]] = {
    "operator_failure_modes": {"operator_flow", "validation_path"},
    "system_failure_modes": {"state_mutation", "dispatch_path", "truth_boundary", "task_register_flow", "ledger_evidence", "writeback_boundary"},
    "concurrency_failure_modes": {"ownership_control", "dispatch_path"},
    "recovery_failure_modes": {"recovery_path", "recovery_responsibility", "reconciliation_path", "writeback_boundary"},
}
INVARIANT_BUCKET_PATTERNS: Dict[str, Tuple[str, ...]] = {
    "recovery": ("recovery", "repair", "reconcile", "reconciliation", "resume", "restart", "checkpoint", "replay"),
    "concurrency": ("ownership", "claim", "lease", "duplicate", "concurrent", "parallel", "fencing", "idempot"),
    "operator_truth": ("operator", "manual", "approval", "audit", "override", "human"),
    "persistence": ("durable", "persist", "commit", "writeback", "ledger", "receipt", "lineage"),
    "ranking_evidence": ("ranking", "evidence", "freshness", "applicability", "retrieval", "answer"),
    "projection_truth": ("projection", "snapshot", "status", "unreachable", "degraded", "truth"),
    "validation": ("validation", "validate", "guard", "precondition", "reject", "safety"),
}
INVARIANT_BUCKET_COVERS: Dict[str, Set[str]] = {
    "recovery": {"recovery_path", "recovery_responsibility", "reconciliation_path"},
    "concurrency": {"ownership_control", "dispatch_path"},
    "operator_truth": {"operator_flow", "validation_path"},
    "persistence": {"state_mutation", "ledger_evidence", "task_register_flow", "truth_boundary", "writeback_boundary"},
    "ranking_evidence": {"validation_path", "truth_boundary", "retrieval_owner", "retrieval_scoring", "retrieval_search", "retrieval_answer", "retrieval_rules"},
    "projection_truth": {"truth_boundary", "operator_flow"},
    "validation": {"validation_path"},
}
FLAG_BUCKETS: Dict[str, Tuple[str, ...]] = {
    "recovery_integrity": ("recovery",),
    "stale_state_handling": ("recovery", "projection_truth"),
    "claim_integrity": ("concurrency", "recovery"),
    "ownership_control": ("concurrency", "persistence"),
    "concurrency_control": ("concurrency",),
    "duplicate_suppression": ("concurrency", "recovery"),
    "idempotency": ("concurrency", "recovery"),
    "operator_safety": ("operator_truth", "validation"),
    "auditability": ("operator_truth", "persistence"),
    "validation_rigor": ("validation", "operator_truth"),
    "state_consistency": ("persistence", "validation"),
    "mutation_boundary": ("persistence",),
    "observability": ("persistence", "operator_truth"),
}
PASSIVE_TOKEN_STOPWORDS = {"only", "from", "read", "build", "service", "bundle", "output", "results", "status"}
PASSIVE_CATEGORY_FALLBACK_TOKENS: Dict[str, Set[str]] = {
    "viewers": {"view", "viewer", "dashboard", "panel", "display", "table"},
    "renderers": {"render", "renderer", "template", "layout", "html", "markdown"},
    "reports": {"report", "coverage", "benchmark", "comparison", "timing", "summary"},
    "status_formatters": {"format", "formatter", "summary", "status", "console"},
    "generated_outputs": {"generated", "autogenerated", "compiled", "bundled", "scaffolded", "snapshot"},
    "read_only_adapters": {"projection", "snapshot", "query", "adapter", "reader", "read", "exporter"},
    "exporters": {"export", "csv", "json", "html", "pdf", "markdown", "zip"},
}
STAGE7_EXECUTION_CONTRACT_FILE = "07_execution_contract.json"
DEFAULT_EXECUTION_CONTRACT_BUDGETS = {
    "max_total_chars": 80000,
    "max_full_file_chars": 25000,
    "hard_full_file_chars": 60000,
    "max_generation_files": 12,
    "total_chars": 80000,
    "full_file_chars": 25000,
    "max_full_files": 3,
    "max_files": 12,
    "max_snippets": 6,
}
DEFAULT_MAX_TARGETS_PER_SUBSYSTEM = 2
ANCHOR_CONFIDENCE_THRESHOLD = 0.75
CENTRALITY_DEMOTION_TOKENS = {"adapter", "binding", "wrapper", "shim", "bridge", "proxy"}
PLUMBING_SURFACE_TOKENS = {"server", "agent", "handler", "router", "orchestrator", "delegate", "dispatch"}
CONTAMINATION_PATH_MARKERS = (
    "infra/pipeline/knowledge_drafts/",
    "tests/benchmarks/pipeline/",
    "/benchmark-gpt/",
    "/pis_datasets/",
    "/_results/",
    "scorecard_",
    "gpt_benchmark_prompt_",
    ".dataset.yaml",
    ".dataset.md",
)
SCHEDULER_NARROW_OWNER_MARKERS = {"watchdog", "healer", "retry", "activation", "claim", "eligibility", "deadlock", "paused", "pause", "resume", "postpone"}
UI_STATIC_EXTENSIONS = (".html", ".css", ".scss", ".sass")
MOBILE_UI_PATH_TERMS = ("infra/mobile/android/", "app/src/main/java/", "app/src/test/java/", "chatviewmodel", "scheduledjobsscreen", "androidviewmodel")
MOBILE_QUERY_TOKENS = {"android", "mobile", "chat", "chatviewmodel", "httpchatapi", "adminapi", "adminviewmodel", "scheduledjobsscreen", "app"}
SCHEDULER_QUERY_TOKENS = {"scheduler", "scheduled", "watchdog", "healer", "retry", "claim", "queue", "job", "jobs", "cron"}


def _coerce_mapping(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _coerce_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(text)
    return out


def _is_contamination_surface(path: str) -> bool:
    rel = str(path or "").replace("\\", "/").lower()
    return any(marker in rel for marker in CONTAMINATION_PATH_MARKERS)


def _is_non_implementation_governance_surface(path: str) -> bool:
    rel = str(path or "").replace("\\", "/").lower()
    return (
        rel.startswith("governance/ledger/patch_instruction_sheets/")
        or rel.startswith("governance/ledger/gold_items/")
        or rel.startswith("governance/ledger/gold_items_large/")
        or rel.startswith("governance/ledger/__gold_benchmark_gpt_items/")
    )


def _normalize_match_key(value: Any) -> str:
    lowered = str(value or "").strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    return re.sub(r"[_\-]+", " ", lowered)


def _contains_phrase(text: Any, phrase: Any) -> bool:
    haystack = _normalize_match_key(text)
    needle = _normalize_match_key(phrase)
    if not haystack or not needle:
        return False
    if " " in needle:
        return needle in haystack
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


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

    seen: Set[str] = set()
    for candidate in candidates:
        normalized = str(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if explicit_knowledge_path:
            return candidate
        manifest = resolve_knowledge_asset_paths(candidate, config or {})
        exists = _coerce_mapping(manifest.get("exists"))
        if all(bool(exists.get(name)) for name in PLANNING_ASSET_NAMES):
            return candidate
    return LOCAL_KNOWLEDGE_REPO_ROOT.resolve()


def _load_planning_assets(repo_root: str | Path, config: Optional[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    assets_repo_root = _resolve_assets_repo_root(repo_root, config)
    return load_knowledge_assets(assets_repo_root, config or {}, PLANNING_ASSET_NAMES)


def _scoring_weight(assets: Mapping[str, Any], key: str, default: float) -> float:
    scoring = _coerce_mapping(assets.get("scoring_weights"))
    retrieval_scoring = _coerce_mapping(scoring.get("retrieval_scoring"))
    entry = _coerce_mapping(retrieval_scoring.get(key))
    weight = entry.get("weight")
    if isinstance(weight, (int, float)) and not isinstance(weight, bool):
        return float(abs(weight))
    return float(default)


def _flatten_requirement_texts(intake_assessment: Mapping[str, Any]) -> List[str]:
    derived = _coerce_mapping(intake_assessment.get("derived_requirements"))
    return _dedupe_strings(
        list(intake_assessment.get("invariants") or [])
        + list(derived.get("safety_requirements") or [])
        + list(derived.get("observability_requirements") or [])
        + list(derived.get("recovery_guarantees") or [])
    )


def _active_failure_categories(intake_assessment: Mapping[str, Any]) -> List[str]:
    taxonomy = _coerce_mapping(intake_assessment.get("failure_taxonomy"))
    return [
        key
        for key in ["operator_failure_modes", "system_failure_modes", "concurrency_failure_modes", "recovery_failure_modes"]
        if list(taxonomy.get(key) or [])
    ]


def _active_invariant_buckets(
    intake_assessment: Mapping[str, Any],
    task_profile: Mapping[str, Any],
    search_plan: Mapping[str, Any],
    families: Sequence[str],
) -> List[str]:
    active: Set[str] = set()
    texts = [str(item or "") for item in _flatten_requirement_texts(intake_assessment)]
    texts.extend(str(item or "") for item in _coerce_mapping(search_plan).get("query_coverage_map", {}).keys())
    texts.extend(str(item or "") for item in list(task_profile.get("cross_cutting_flags") or []))
    family_set = _family_alias_set(families)
    if _is_retrieval_ranking_family(families, task_profile):
        active.add("ranking_evidence")
    if _is_ui_truth_projection_family(families, task_profile):
        active.add("projection_truth")
    if _is_truth_contract_family(families, task_profile):
        active.update({"persistence", "operator_truth"})
    for text in texts:
        lowered = _normalize_match_key(text)
        for bucket, patterns in INVARIANT_BUCKET_PATTERNS.items():
            if any(pattern in lowered for pattern in patterns):
                active.add(bucket)
    if not active:
        active.update({"validation", "persistence"})
    return sorted(active)


def _query_cover_tags(search_plan: Mapping[str, Any]) -> List[str]:
    tags: List[str] = []
    for meta in _coerce_mapping(search_plan.get("query_coverage_map")).values():
        entry = _coerce_mapping(meta)
        tags.extend(str(item or "") for item in list(entry.get("covers") or []))
        tags.extend(str(item or "") for item in list(entry.get("cross_cutting_flags") or entry.get("flags") or []))
    return _dedupe_strings(tags)


def _candidate_coverage_buckets(covers: Set[str], path_lower: str, matched_terms: Set[str]) -> Set[str]:
    buckets: Set[str] = set()
    for bucket, cover_tags in INVARIANT_BUCKET_COVERS.items():
        if covers.intersection(cover_tags):
            buckets.add(bucket)
    joined = " ".join([path_lower] + sorted(matched_terms))
    if any(token in joined for token in ("ranking", "evidence", "freshness", "applicability", "retrieval")):
        buckets.add("ranking_evidence")
    if any(token in joined for token in ("projection", "snapshot", "unreachable", "degraded", "reconnecting")):
        buckets.add("projection_truth")
    return buckets


def _candidate_failure_categories(covers: Set[str], path_lower: str) -> List[str]:
    categories: List[str] = []
    for category, cover_tags in FAILURE_CATEGORY_COVER_MAP.items():
        if covers.intersection(cover_tags):
            categories.append(category)
    if "claim" in path_lower or "lease" in path_lower:
        categories.append("concurrency_failure_modes")
    return _dedupe_strings(categories)


def _candidate_flag_matches(covers: Set[str], path_lower: str, active_flags: Sequence[str]) -> List[str]:
    matched: List[str] = []
    buckets = _candidate_coverage_buckets(covers, path_lower, set())
    for flag in [str(item or "") for item in active_flags if str(item or "").strip()]:
        expected = set(FLAG_BUCKETS.get(flag, ()))
        if expected and buckets.intersection(expected):
            matched.append(flag)
            continue
        if flag in {"operator_safety", "validation_rigor"} and covers.intersection({"operator_flow", "validation_path"}):
            matched.append(flag)
        elif flag in {"state_consistency", "mutation_boundary"} and covers.intersection({"state_mutation", "truth_boundary", "task_register_flow", "ledger_evidence"}):
            matched.append(flag)
        elif flag in {"recovery_integrity", "stale_state_handling"} and covers.intersection({"recovery_path", "recovery_responsibility", "reconciliation_path"}):
            matched.append(flag)
        elif flag in {"concurrency_control", "ownership_control", "duplicate_suppression", "claim_integrity", "idempotency"} and (covers.intersection({"ownership_control", "dispatch_path"}) or "claim" in path_lower or "lease" in path_lower):
            matched.append(flag)
    return _dedupe_strings(matched)


def _score_candidate_coverage_v2(
    candidate: Mapping[str, Any],
    *,
    intake_assessment: Mapping[str, Any],
    task_profile: Mapping[str, Any],
    search_plan: Mapping[str, Any],
    candidate_subsystems: Sequence[str],
    ownership_role: str,
    candidate_role: str,
    families: Sequence[str],
    assets: Mapping[str, Any],
) -> Dict[str, Any]:
    covers = {str(item or "").strip().lower() for item in list(candidate.get("covers") or []) if str(item or "").strip()}
    path_lower = str(candidate.get("path") or "").lower()
    matched_terms = {str(item or "").lower() for item in list(candidate.get("matched_terms") or []) if str(item or "").strip()}
    active_failure_categories = _active_failure_categories(intake_assessment)
    active_invariant_buckets = _active_invariant_buckets(intake_assessment, task_profile, search_plan, families)
    query_cover_tags = {str(item or "").strip().lower() for item in _query_cover_tags(search_plan)}
    query_subsystems = {str(item or "").strip() for item in list(search_plan.get("subsystem_hypotheses") or []) if str(item or "").strip()}
    active_flags = [str(item or "") for item in list(task_profile.get("cross_cutting_flags") or []) if str(item or "").strip()]

    failure_categories = [item for item in _candidate_failure_categories(covers, path_lower) if item in active_failure_categories]
    coverage_buckets = _candidate_coverage_buckets(covers, path_lower, matched_terms)
    invariant_buckets = [bucket for bucket in coverage_buckets if bucket in active_invariant_buckets]
    flag_matches = _candidate_flag_matches(covers, path_lower, active_flags)
    subsystem_matches = [subsystem for subsystem in _dedupe_strings(candidate_subsystems) if subsystem in query_subsystems]
    query_cover_matches = [tag for tag in _dedupe_strings(query_cover_tags) if tag.lower() in covers or tag.lower() in flag_matches]

    failure_weight = 0.34
    invariant_weight = max(0.12, min(0.32, _scoring_weight(assets, "invariant_signal_bonus", 0.09) * 2.0))
    flag_weight = max(0.1, min(0.24, _scoring_weight(assets, "cross_cutting_flag_match", 0.07) * 2.0))
    subsystem_weight = 0.22
    query_cover_weight = 0.16
    ownership_weight = max(0.14, min(0.3, _scoring_weight(assets, "ownership_signal_bonus", 0.11) * 1.8))

    score = (
        len(failure_categories) * failure_weight
        + len(invariant_buckets[:4]) * invariant_weight
        + len(flag_matches[:4]) * flag_weight
        + len(subsystem_matches[:3]) * subsystem_weight
        + len(query_cover_matches[:3]) * query_cover_weight
    )
    if ownership_role == "core_owner" and score > 0:
        score += ownership_weight
    elif ownership_role == "test_surface" and (flag_matches or failure_categories):
        score += ownership_weight * 0.5
    if candidate_role == "implementation_code" and score > 0:
        score += 0.12
    score = min(2.6, score)
    return {
        "score": round(score, 4),
        "matched_failure_categories": failure_categories,
        "matched_invariant_buckets": _dedupe_strings(invariant_buckets),
        "matched_cross_cutting_flags": flag_matches,
        "matched_subsystems": _dedupe_strings(subsystem_matches),
        "matched_query_coverage": _dedupe_strings(query_cover_matches),
        "coverage_breadth": len(set(failure_categories).union(invariant_buckets).union(flag_matches).union(subsystem_matches).union(query_cover_matches)),
    }


def _passive_category_lexicon(passive_asset: Mapping[str, Any]) -> Dict[str, Set[str]]:
    patterns = _coerce_mapping(passive_asset.get("patterns"))
    out: Dict[str, Set[str]] = {}
    for category, values in patterns.items():
        tokens: Set[str] = set(PASSIVE_CATEGORY_FALLBACK_TOKENS.get(str(category), set()))
        for value in list(values or []):
            for token in re.findall(r"[a-z]{4,}", _normalize_match_key(value)):
                if token not in PASSIVE_TOKEN_STOPWORDS:
                    tokens.add(token)
        if tokens:
            out[str(category)] = tokens
    return out


def _score_passive_relevance_penalty(
    candidate: Mapping[str, Any],
    *,
    passive_asset: Mapping[str, Any],
    families: Sequence[str],
    task_profile: Mapping[str, Any],
    ownership_role: str,
    candidate_role: str,
    file_role: str,
    covers: Set[str],
    path_lower: str,
) -> Dict[str, Any]:
    if _is_docs_task(families, task_profile) and candidate_role in {"documentation", "governance_doc"}:
        return {"score": 0.0, "categories": [], "reasons": []}
    previews = " ".join(str(item.get("preview") or "") for item in list(candidate.get("line_hits") or [])[:4]).lower()
    search_blob = " ".join([path_lower, previews, " ".join(str(item or "") for item in list(candidate.get("matched_terms") or []))]).lower()
    lexicon = _passive_category_lexicon(passive_asset)
    categories: List[str] = []
    reasons: List[str] = []
    for category, tokens in lexicon.items():
        hits = [token for token in tokens if token in search_blob]
        if len(hits) >= 2:
            categories.append(category)
            reasons.append(f"matches passive {category.replace('_', ' ')} tokens")
    if file_role in {"report_summary", "generated_artifact", "log_trace"}:
        categories.append("generated_outputs")
        reasons.append("generated or report-like surface")
    if any(token in path_lower for token in ["knowledge_drafts", "/reports/", "/runs/", "/artifacts/", "benchmark", "summary", "trace", "registry"]):
        categories.append("reports")
        reasons.append("historical, benchmark, or registry-style surface")
    if _is_contamination_surface(path_lower):
        categories.append("generated_outputs")
        reasons.append("draft benchmark or dataset contamination surface")
    if any(token in path_lower for token in ["snapshot", "projection", "status", "viewer", "renderer", "route", "router"]):
        categories.append("read_only_adapters")
        reasons.append("projection or route-like surface without mutation ownership")
    categories = _dedupe_strings(categories)
    score = 0.0
    if categories and ownership_role != "core_owner" and not covers.intersection(OWNER_SIGNAL_COVERS):
        score += min(1.6, 0.45 + 0.28 * len(categories))
    if ownership_role in {"projection", "read_only_route", "hub"} and not _is_ui_truth_projection_family(families, task_profile):
        score += 0.55
    if candidate_role in {"temporary_artifact", "documentation", "governance_doc"} and not _prefers_docs_reference(families, task_profile):
        score += 0.35
    if _is_ui_truth_projection_family(families, task_profile) and ownership_role == "core_owner":
        score *= 0.15
    return {"score": round(min(2.8, score), 4), "categories": categories, "reasons": _dedupe_strings(reasons)}


def _score_superficial_match_penalty_v2(
    candidate: Mapping[str, Any],
    *,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
    candidate_role: str,
    file_role: str,
    path_lower: str,
    lexical_overlap: int,
    matched_symbols: Set[str],
    covers: Set[str],
    hub_kind: str,
    explicit_path_request: bool,
    owner_cover_count: int,
    graph_score: float,
    ownership_role: str,
) -> Dict[str, Any]:
    reasons: List[str] = []
    score = 0.0
    line_hits = list(candidate.get("line_hits") or [])
    if lexical_overlap > 0 and not matched_symbols and not covers and graph_score <= 0.2:
        score += 0.85
        reasons.append("weak lexical-only overlap")
    if explicit_path_request and lexical_overlap > 0 and not matched_symbols and not covers and len(line_hits) <= 1:
        score += 0.55
        reasons.append("filename-only relevance without ownership evidence")
    if hub_kind and owner_cover_count == 0:
        score += 0.7
        reasons.append("central-looking hub without owner signals")
    if candidate_role in {"temporary_artifact"} or file_role in {"generated_artifact", "report_summary", "log_trace"}:
        score += 1.05
        reasons.append("generated, artifact, or benchmark contamination")
    if any(token in path_lower for token in ["knowledge_drafts", "/reports/", "/artifacts/", "benchmark", "gold_suite", "draft"]) or _is_contamination_surface(path_lower):
        score += 0.85
        reasons.append("draft or benchmark path contamination")
    if lexical_overlap >= 2 and owner_cover_count == 0 and not covers.intersection(OWNER_SIGNAL_COVERS):
        score += 0.35
        reasons.append("mentions active concepts without owning the logic")
    if _is_ui_truth_projection_family(families, task_profile) and ownership_role == "core_owner":
        score *= 0.2
    return {"score": round(min(3.2, score), 4), "reasons": _dedupe_strings(reasons)}


def _score_bundle_diversity(task_archetype: str, files: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    bundle_roles = {str(entry.get("bundle_role") or entry.get("role") or "") for entry in files if str(entry.get("bundle_role") or entry.get("role") or "").strip()}
    file_roles = {str(entry.get("file_role") or "") for entry in files if str(entry.get("file_role") or "").strip()}
    score = 0.0
    markers: List[str] = []
    if task_archetype == "implementation_change":
        if bundle_roles.intersection({"primary_target", "secondary_target", "config_target", "edit_target"}):
            score += 0.45
            markers.append("owner_or_edit_target")
        if bundle_roles.intersection({"paired_test", "validation_target", "validation_context"}) or "test_code" in file_roles:
            score += 0.35
            markers.append("test_surface")
        if bundle_roles.intersection({"dependency_context", "integration_context", "boundary_context"}) or "implementation_code" in file_roles:
            score += 0.15
            markers.append("support_context")
        if bundle_roles.intersection({"interface_contract_context", "new_file_support_context", "reference_context"}) or file_roles.intersection({"documentation", "schema_contract"}):
            score += 0.1
            markers.append("reference_context")
    elif task_archetype == "design_new_surface":
        if bundle_roles.intersection({"interface_contract_context", "reference_context"}) or file_roles.intersection({"documentation", "schema_contract"}):
            score += 0.4
            markers.append("interface_contract_context")
        if bundle_roles.intersection({"integration_context", "integration_target", "new_file_support_context"}):
            score += 0.4
            markers.append("integration_context")
        if "test_code" in file_roles or bundle_roles.intersection({"paired_test", "validation_context"}):
            score += 0.2
            markers.append("validation_context")
    elif task_archetype == "repo_assessment":
        representative = sum(1 for key in ("implementation_code", "test_code", "config_build", "documentation", "schema_contract") if key in file_roles)
        score += min(1.0, representative / 4.0)
        markers.extend(sorted(file_roles))
    else:
        representative = sum(1 for key in ("implementation_code", "test_code", "config_build", "documentation", "schema_contract") if key in file_roles)
        score += min(1.0, representative / 5.0)
        markers.extend(sorted(bundle_roles))
    return {"score": round(min(1.0, score), 4), "markers": _dedupe_strings(markers)}


def _bundle_rule_for_families(bundle_rules_asset: Mapping[str, Any], families: Sequence[str]) -> Dict[str, Any]:
    required = _coerce_mapping(bundle_rules_asset.get("required_coverage"))
    family_set = _family_alias_set(families)
    for key in [
        "scheduler_safety",
        "operator_control",
        "queue_dispatch",
        "distributed_coordination",
        "reconciliation_recovery",
        "persistence_integrity",
        "stateful_workflow",
        "api_control_plane",
    ]:
        if key in family_set and isinstance(required.get(key), Mapping):
            return _coerce_mapping(required.get(key))
    return {}


def _build_bundle_coverage_validation(
    *,
    files: Sequence[Mapping[str, Any]],
    composition: Mapping[str, Any],
    intake_assessment: Mapping[str, Any],
    task_profile: Mapping[str, Any],
    search_plan: Mapping[str, Any],
    families: Sequence[str],
    assets: Mapping[str, Any],
    bundle_confidence: float,
) -> Dict[str, Any]:
    active_failure_categories = _active_failure_categories(intake_assessment)
    covered_failure_categories = _dedupe_strings(
        item
        for entry in files
        for item in list(entry.get("covers_failure_modes") or [])
    )
    active_subsystems = _dedupe_strings(search_plan.get("subsystem_hypotheses") or [])
    covered_subsystems = _dedupe_strings(
        subsystem
        for entry in files
        for subsystem in list(entry.get("subsystems") or [])
    )
    task_archetype = _task_archetype(task_profile)
    cross_flags = _task_cross_flags(task_profile, intake_assessment)
    diversity = _score_bundle_diversity(task_archetype, files)
    rule = _bundle_rule_for_families(_coerce_mapping(assets.get("bundle_coverage_rules")), families)
    required_subsystem_coverage = _dedupe_strings(rule.get("required_subsystem_coverage") or [])
    uncovered_failure_categories = [item for item in active_failure_categories if item not in covered_failure_categories]
    uncovered_active_subsystems = [item for item in active_subsystems if item not in covered_subsystems]
    uncovered_required_subsystems = [item for item in required_subsystem_coverage if item not in covered_subsystems]
    test_surface_present = any(
        str(entry.get("candidate_role") or "") == "test_file"
        or str(entry.get("ownership_role") or "") == "test_surface"
        or str(entry.get("bundle_role") or entry.get("role") or "") in {"paired_test", "validation_target", "validation_context"}
        or str(entry.get("file_role") or "") == "test_code"
        for entry in files
    )
    core_owner_present = any(str(entry.get("ownership_role") or "") == "core_owner" for entry in files)
    scheduler_cross_surface_expected = bool(
        _is_scheduler_family(families)
        and (
            _primary_task_type(task_profile) == "system_hardening"
            or bool(active_failure_categories)
            or len(active_subsystems) > 1
            or bool(cross_flags.intersection(RECOVERY_FOCUS_FLAGS.union(OPERATOR_FOCUS_FLAGS).union(VALIDATION_FOCUS_FLAGS)))
        )
    )
    retrieval_owner_present = any(
        _family_surface_categories(entry, families=families, task_profile=task_profile).intersection({"retrieval_owner"})
        for entry in files
    )
    retrieval_shape_present = any(
        _family_surface_categories(entry, families=families, task_profile=task_profile).intersection({"retrieval_scoring", "retrieval_search", "retrieval_answer"})
        for entry in files
    )
    projection_owner_present = any(
        str(entry.get("ownership_role") or "") == "core_owner"
        and any(token in str(entry.get("path") or "") for token in ["snapshot", "projection", "status", "health"])
        for entry in files
    )
    projection_ui_present = any(
        "ui_truth_projection" in _family_surface_categories(entry, families=families, task_profile=task_profile)
        for entry in files
    )
    focused_failure_coverage_satisfied = bool(
        (_is_retrieval_ranking_family(families, task_profile) and retrieval_owner_present and retrieval_shape_present and test_surface_present)
        or (_is_ui_truth_projection_family(families, task_profile) and projection_owner_present and projection_ui_present and test_surface_present)
    )
    warning_markers: List[str] = []
    warnings: List[str] = list(composition.get("bundle_weakness_reasons") or [])
    if uncovered_failure_categories and not focused_failure_coverage_satisfied:
        warnings.append("Active failure categories are not represented by the selected bundle")
        warning_markers.append("missing_failure_category_coverage")
    if uncovered_required_subsystems:
        warnings.append("Required subsystem coverage from bundle rules is still missing")
        warning_markers.append("missing_required_subsystems")
    if rule.get("test_files_required") and not test_surface_present and (not _is_scheduler_family(families) or scheduler_cross_surface_expected):
        warnings.append("Test-sensitive task lacks an explicit test surface in the selected bundle")
        warning_markers.append("missing_test_surface")
    if _is_retrieval_ranking_family(families, task_profile) and not any(str(entry.get("path") or "").startswith("tests/pipeline/") for entry in files):
        warnings.append("Ranking or evidence task lacks paired pipeline validation coverage")
        warning_markers.append("missing_ranking_tests")
    if _is_retrieval_ranking_family(families, task_profile) and not retrieval_owner_present:
        warnings.append("Retrieval-ranking bundle lacks a deterministic ranking owner surface")
        warning_markers.append("missing_core_owner")
    if _is_retrieval_ranking_family(families, task_profile) and not retrieval_shape_present:
        warnings.append("Retrieval-ranking bundle lacks search-plan or scoring context alongside the primary owner")
        warning_markers.append("missing_retrieval_shape")
    if _is_ui_truth_projection_family(families, task_profile) and not projection_owner_present:
        warnings.append("Truth/projection task lacks the projection owner surface")
        warning_markers.append("missing_projection_owner")
    if _is_ui_truth_projection_family(families, task_profile) and not projection_ui_present:
        warnings.append("Truth/projection task lacks a projection-facing UI surface")
        warning_markers.append("missing_projection_ui")
    if _is_truth_contract_family(families, task_profile) and not any(
        set(entry.get("responsibility") or []).intersection({"truth_boundary", "task_register_flow", "writeback_boundary"})
        or _family_surface_categories(entry, families=families, task_profile=task_profile).intersection({"truth_state", "truth_ownership", "truth_writeback"})
        for entry in files
    ):
        warnings.append("Truth-boundary task lacks a canonical truth or writeback boundary file")
        warning_markers.append("missing_truth_boundary")
    if _is_truth_contract_family(families, task_profile) and not any(
        _family_surface_categories(entry, families=families, task_profile=task_profile).intersection({"truth_reconciliation", "truth_writeback"})
        for entry in files
    ):
        warnings.append("Truth-boundary task lacks reconciliation or writeback adjacency in the bundle")
        warning_markers.append("missing_truth_shape")
    if scheduler_cross_surface_expected and not any(
        "scheduler_recovery" in _family_surface_categories(entry, families=families, task_profile=task_profile)
        for entry in files
    ):
        warnings.append("Scheduler bundle lacks watchdog, healer, or recovery-adjacent surface")
        warning_markers.append("missing_scheduler_recovery")
    if scheduler_cross_surface_expected and not any(
        "scheduler_ui" in _family_surface_categories(entry, families=families, task_profile=task_profile)
        for entry in files
    ):
        warnings.append("Scheduler bundle lacks operator or UI surface for recovery-state visibility")
        warning_markers.append("missing_scheduler_ui")
    owner_sensitive_gap_check = bool(
        active_failure_categories
        or required_subsystem_coverage
        or rule.get("test_files_required")
        or _is_retrieval_ranking_family(families, task_profile)
        or _is_ui_truth_projection_family(families, task_profile)
        or _is_truth_contract_family(families, task_profile)
    )
    if not core_owner_present and task_archetype == "implementation_change" and owner_sensitive_gap_check:
        warnings.append("Implementation bundle has no dominant owner surface")
        warning_markers.append("missing_core_owner")
    coverage_ratio = 1.0
    if active_failure_categories:
        coverage_ratio = len(covered_failure_categories) / max(1, len(active_failure_categories))
    if bundle_confidence >= 0.72 and (coverage_ratio < 0.5 or diversity.get("score", 0.0) < 0.35):
        warnings.append("Bundle confidence is higher than the actual coverage breadth or diversity supports")
        warning_markers.append("confidence_overstates_coverage")
    return {
        "bundle_ready_for_execution": bool(composition.get("bundle_ready_for_task")),
        "active_failure_categories": active_failure_categories,
        "covered_failure_categories": covered_failure_categories,
        "uncovered_failure_categories": uncovered_failure_categories,
        "active_subsystems": active_subsystems,
        "covered_subsystems": covered_subsystems,
        "uncovered_subsystems": uncovered_active_subsystems,
        "required_subsystem_coverage": required_subsystem_coverage,
        "uncovered_required_subsystems": uncovered_required_subsystems,
        "test_surface_present": bool(test_surface_present),
        "core_owner_present": bool(core_owner_present),
        "bundle_diversity_score": diversity.get("score"),
        "bundle_diversity_markers": list(diversity.get("markers") or []),
        "bundle_coverage_score": round(min(1.0, coverage_ratio), 4),
        "warning_markers": _dedupe_strings(warning_markers),
        "weakness_reasons": _dedupe_strings(warnings),
    }


def _subsystem_entries(subsystem_asset: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [_coerce_mapping(raw) for raw in _coerce_list(subsystem_asset.get("subsystems")) if _coerce_mapping(raw).get("key")]


def _entry_terms(entry: Mapping[str, Any]) -> List[str]:
    return _dedupe_strings(
        [entry.get("key")] + list(entry.get("aliases") or []) + list(entry.get("filename_aliases") or []) + list(entry.get("doc_aliases") or [])
    )


def _query_coverage_subsystem_index(search_plan: Mapping[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for query_text, raw_meta in _coerce_mapping(search_plan.get("query_coverage_map")).items():
        meta = _coerce_mapping(raw_meta)
        subsystems = _dedupe_strings(meta.get("subsystems") or meta.get("subsystem") or [])
        key = _normalize_match_key(query_text)
        if key and subsystems:
            out[key] = subsystems
    return out


def _subsystem_filename_alias_index(subsystem_asset: Mapping[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for entry in _subsystem_entries(subsystem_asset):
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        for alias in _coerce_list(entry.get("filename_aliases")):
            basename = Path(str(alias or "")).name.lower()
            if not basename:
                continue
            out.setdefault(basename, []).append(key)
    return {name: _dedupe_strings(values) for name, values in out.items()}


def _infer_candidate_subsystems(
    candidate: Mapping[str, Any],
    *,
    search_plan: Mapping[str, Any],
    subsystem_asset: Mapping[str, Any],
    query_subsystem_index: Mapping[str, Sequence[str]],
    filename_alias_index: Mapping[str, Sequence[str]],
) -> List[str]:
    scores: Dict[str, float] = {}
    path = str(candidate.get("path") or "").strip()
    basename = Path(path).name.lower()
    matched_values = _dedupe_strings(list(candidate.get("matched_terms") or []) + list(candidate.get("matched_symbols") or []))
    hypothesis_set = {str(item or "").strip() for item in list(search_plan.get("subsystem_hypotheses") or []) if str(item or "").strip()}

    for value in matched_values:
        for subsystem in query_subsystem_index.get(_normalize_match_key(value), []):
            scores[str(subsystem)] = scores.get(str(subsystem), 0.0) + 2.4

    for subsystem in filename_alias_index.get(basename, []):
        scores[str(subsystem)] = scores.get(str(subsystem), 0.0) + 3.0

    signal_tokens = _word_set(" ".join([path] + matched_values))
    covers = {str(item or "").strip().lower() for item in list(candidate.get("covers") or []) if str(item or "").strip()}
    for entry in _subsystem_entries(subsystem_asset):
        key = str(entry.get("key") or "").strip()
        if not key:
            continue
        if hypothesis_set and key not in hypothesis_set and key not in scores and key not in filename_alias_index.get(basename, []):
            continue
        token_hits: Set[str] = set()
        for term in _entry_terms(entry):
            term_tokens = {token for token in _word_set(term) if token not in CORE_HUB_GENERIC_TOKENS}
            token_hits.update(term_tokens.intersection(signal_tokens))
            if " " in str(term) and _contains_phrase(path + " " + " ".join(matched_values), term):
                scores[key] = scores.get(key, 0.0) + 0.4
        if token_hits:
            scores[key] = scores.get(key, 0.0) + min(1.0, 0.22 * len(token_hits))

    if "operator_flow" in covers:
        scores["operator_entrypoints"] = scores.get("operator_entrypoints", 0.0) + 1.15
    if covers.intersection({"recovery_responsibility", "recovery_path"}):
        scores["recovery_mechanisms"] = scores.get("recovery_mechanisms", 0.0) + 1.05
    if "dispatch_path" in covers:
        scores["scheduler_core"] = scores.get("scheduler_core", 0.0) + 0.85
    if "validation_path" in covers:
        scores["validation"] = scores.get("validation", 0.0) + 0.95
    if "state_mutation" in covers:
        scores["state_persistence"] = scores.get("state_persistence", 0.0) + 0.8
    if "truth_boundary" in covers:
        scores["truth_ownership"] = scores.get("truth_ownership", 0.0) + 0.9
    if "task_register_flow" in covers:
        scores["task_register"] = scores.get("task_register", 0.0) + 0.9
    if "retrieval_owner" in covers:
        scores["retrieval_ranker"] = scores.get("retrieval_ranker", 0.0) + 1.1
        scores["answer_assembly"] = scores.get("answer_assembly", 0.0) + 0.45
    if "retrieval_search" in covers:
        scores["query_shaping"] = scores.get("query_shaping", 0.0) + 1.0
    if "retrieval_scoring" in covers:
        scores["retrieval_ranker"] = scores.get("retrieval_ranker", 0.0) + 0.95
    if "retrieval_answer" in covers:
        scores["answer_assembly"] = scores.get("answer_assembly", 0.0) + 1.0
    if "retrieval_rules" in covers:
        scores["policy_assets"] = scores.get("policy_assets", 0.0) + 0.8

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [key for key, score in ranked if score > 0][:2]


def _candidate_primary_subsystem(entry: Mapping[str, Any]) -> Optional[str]:
    subsystems = _dedupe_strings(entry.get("subsystems") or [])
    if subsystems:
        return subsystems[0]
    hub_kind = str(entry.get("hub_kind") or "").strip()
    if hub_kind == "webui_orchestrator_hub":
        return "agent_orchestration"
    if hub_kind == "webui_router_hub":
        return "route_surface"
    if hub_kind == "scheduler_hub":
        return "scheduler_core"
    if hub_kind in {"pipeline_planner_hub", "pipeline_stage_hub"}:
        return "orchestration_engine"
    return None


def _is_forbidden_implementation_target(path: str) -> bool:
    rel = str(path or "").replace("\\", "/").strip("/").lower()
    if not rel:
        return False
    parts = Path(rel).parts
    if _is_non_implementation_governance_surface(rel):
        return True
    if rel.endswith(".yaml"):
        return True
    if rel == "infra/pipeline/knowledge.zip":
        return True
    if len(parts) >= 3 and parts[0] == "tools" and parts[1] == "pipeline" and parts[2] == "knowledge":
        return True
    if "knowledge_drafts" in parts or (len(parts) >= 2 and parts[0] == "benchmarks" and parts[1] == "pipeline"):
        return True
    return False


def _needs_anchor_injection(search_plan: Mapping[str, Any]) -> bool:
    confidence_meta = _coerce_mapping(search_plan.get("confidence"))
    raw_confidence = confidence_meta.get("score")
    confidence = float(raw_confidence) if raw_confidence is not None else 1.0
    subsystem_count = len(_dedupe_strings(search_plan.get("subsystem_hypotheses") or []))
    return confidence < ANCHOR_CONFIDENCE_THRESHOLD or subsystem_count > 1


def _anchor_filename_aliases(subsystem_asset: Mapping[str, Any], subsystem_hypotheses: Sequence[str]) -> Set[str]:
    hypothesis_set = {str(item or "").strip() for item in subsystem_hypotheses if str(item or "").strip()}
    out: Set[str] = set()
    for entry in _subsystem_entries(subsystem_asset):
        key = str(entry.get("key") or "").strip()
        if hypothesis_set and key not in hypothesis_set:
            continue
        for alias in _coerce_list(entry.get("filename_aliases")):
            basename = Path(str(alias or "")).name.lower()
            if basename:
                out.add(basename)
    return out


def _anchor_candidate_score(entry: Mapping[str, Any], anchor_aliases: Set[str], subsystem_hypotheses: Sequence[str]) -> float:
    path = str(entry.get("path") or "")
    basename = Path(path).name.lower()
    score = float(entry.get("graph_score") or 0.0)
    if basename in anchor_aliases:
        score += 2.4
    if str(entry.get("hub_kind") or ""):
        score += 1.8
    if set(_dedupe_strings(entry.get("subsystems") or [])).intersection({str(item or "").strip() for item in subsystem_hypotheses if str(item or "").strip()}):
        score += 0.9
    if str(entry.get("candidate_role") or "") in {"implementation_code", "config_file"}:
        score += 0.4
    if bool(entry.get("explicit_path_request")):
        score += 0.2
    return score


def _as_reference_anchor(entry: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(entry)
    out["selection_lane"] = "reference_context"
    out["edit_allowed"] = False
    out["score"] = float(out.get("reference_value_score") or out.get("score") or 0.0)
    out["reasons"] = _dedupe_strings(list(out.get("reasons") or []) + ["added as anchor context for subsystem breadth"])
    out["anchor_context"] = True
    return out


def _pick_anchor_candidates(
    ranked: Sequence[Mapping[str, Any]],
    *,
    selected_paths: Set[str],
    subsystem_asset: Mapping[str, Any],
    subsystem_hypotheses: Sequence[str],
    anchor_limit: int,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
    query_terms: Set[str],
) -> List[Dict[str, Any]]:
    if anchor_limit <= 0:
        return []
    anchor_aliases = _anchor_filename_aliases(subsystem_asset, subsystem_hypotheses)
    pool: List[Tuple[float, str, Dict[str, Any]]] = []
    for raw in ranked:
        entry = dict(raw)
        path = str(entry.get("path") or "")
        if not path or path in selected_paths:
            continue
        if _is_forbidden_implementation_target(path):
            continue
        if _should_skip_reference_candidate(path, families=families, task_profile=task_profile, query_terms=query_terms):
            continue
        if str(entry.get("candidate_role") or "") not in {"implementation_code", "config_file"}:
            continue
        if not list(entry.get("snippet_candidates") or []):
            continue
        basename = Path(path).name.lower()
        graph_score = float(entry.get("graph_score") or 0.0)
        if not (basename in anchor_aliases or str(entry.get("hub_kind") or "") or graph_score >= 0.8):
            continue
        pool.append((_anchor_candidate_score(entry, anchor_aliases, subsystem_hypotheses), path, entry))
    pool.sort(key=lambda item: (-item[0], item[1]))
    chosen: List[Dict[str, Any]] = []
    chosen_paths: Set[str] = set()
    used_keys: Set[str] = set()
    diversity_goal = max(1, min(anchor_limit, len(_dedupe_strings(subsystem_hypotheses)) or 1))
    for _, path, entry in pool:
        if len(chosen) >= anchor_limit:
            break
        key = _candidate_primary_subsystem(entry) or str(entry.get("hub_kind") or "") or Path(path).stem.lower()
        if key in used_keys and len(used_keys) < diversity_goal:
            continue
        used_keys.add(key)
        chosen_paths.add(path)
        chosen.append(_as_reference_anchor(entry))
    if len(chosen) < anchor_limit:
        for _, path, entry in pool:
            if len(chosen) >= anchor_limit:
                break
            if path in chosen_paths:
                continue
            chosen_paths.add(path)
            chosen.append(_as_reference_anchor(entry))
    return chosen


def _select_targets_with_diversity(
    ordered_targets: Sequence[Mapping[str, Any]],
    *,
    max_slots: int,
    min_primary_keep: int,
    best_primary: float,
    score_margin_keep_secondary: float,
    narrow_task: bool,
    max_targets_per_subsystem: int,
) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    selected_paths: Set[str] = set()
    subsystem_counts: Dict[str, int] = {}

    def eligible(item: Mapping[str, Any]) -> bool:
        hub_kind = str(item.get("hub_kind") or "")
        explicit_path_request = bool(item.get("explicit_path_request"))
        owner_signal_strength = float(item.get("owner_signal_strength") or 0.0)
        hub_surface_penalty = float(item.get("hub_surface_penalty") or 0.0)
        ownership_role = str(item.get("ownership_role") or "")
        if narrow_task and hub_kind and not explicit_path_request and len(selected) >= 2:
            return False
        if hub_kind and not explicit_path_request and ownership_role not in {"core_owner", "test_surface"}:
            if owner_signal_strength < 1.0 and hub_surface_penalty >= 0.9 and len(selected) >= 1:
                return False
            if owner_signal_strength < 0.35 and len(selected) >= 1:
                return False
        return float(item.get("targetability_score") or 0.0) >= best_primary - score_margin_keep_secondary or len(selected) < min_primary_keep

    for item in ordered_targets:
        path = str(item.get("path") or "")
        if len(selected) >= max_slots or not path or not eligible(item):
            continue
        subsystem = _candidate_primary_subsystem(item)
        if subsystem and subsystem_counts.get(subsystem, 0) >= max_targets_per_subsystem:
            continue
        selected.append(dict(item))
        selected_paths.add(path)
        if subsystem:
            subsystem_counts[subsystem] = subsystem_counts.get(subsystem, 0) + 1

    if len(selected) < min(max_slots, min_primary_keep):
        for item in ordered_targets:
            path = str(item.get("path") or "")
            if len(selected) >= max_slots or not path or path in selected_paths or not eligible(item):
                continue
            selected.append(dict(item))
            selected_paths.add(path)

    return selected


def _scheduler_only_targets(selected_targets: Sequence[Mapping[str, Any]]) -> bool:
    if not selected_targets:
        return False
    for item in selected_targets:
        path = str(item.get("path") or "").lower()
        if not (_is_scheduler_surface_path(path) or Path(path).name.lower() == "test_scheduled_jobs.py"):
            return False
    return True


def _rescue_non_scheduler_targets(
    *,
    selected_targets: Sequence[Mapping[str, Any]],
    ranked_targets: Sequence[Mapping[str, Any]],
    ranked_refs: Sequence[Mapping[str, Any]],
    ranked: Sequence[Mapping[str, Any]],
    families: Sequence[str],
    task_profile: Mapping[str, Any],
    query_terms: Set[str],
    target_slot_limit: int,
) -> List[Dict[str, Any]]:
    if _is_scheduler_family(families) or not _scheduler_only_targets(selected_targets):
        return [dict(item) for item in selected_targets]
    mobile_query = _query_mentions_mobile_surface(query_terms)
    current_selected_score = max(
        (float(item.get("targetability_score") or item.get("score") or 0.0) for item in selected_targets),
        default=0.0,
    )
    best_owner: Optional[Dict[str, Any]] = None
    best_score = -999.0
    for raw in list(ranked_targets) + list(ranked_refs):
        entry = dict(raw)
        path = str(entry.get("path") or "")
        if not path:
            continue
        rel = path.lower()
        if _is_non_implementation_governance_surface(rel) or _is_contamination_surface(rel):
            continue
        if _is_scheduler_surface_path(rel) or Path(rel).name.lower() == "test_scheduled_jobs.py":
            continue
        if _is_mobile_ui_path(rel) and not mobile_query and not _is_ui_truth_projection_family(families, task_profile):
            continue
        role = str(entry.get("candidate_role") or "")
        ownership_role = str(entry.get("ownership_role") or "")
        if role not in {"implementation_code", "config_file", "test_file"}:
            continue
        family_fit = float(entry.get("family_owner_fit") or 0.0)
        path_overlap = len(_path_keyword_overlap(rel, query_terms))
        matched_terms = {
            str(term or "").lower()
            for term in list(entry.get("matched_terms") or []) + list(entry.get("matched_symbols") or [])
            if str(term or "").strip()
        }
        lexical_overlap = len({str(token or "").lower() for token in query_terms}.intersection(matched_terms))
        score = float(entry.get("targetability_score") or entry.get("score") or 0.0)
        if ownership_role == "core_owner":
            score += 4.0
        elif ownership_role == "test_surface":
            score += 1.2
        elif ownership_role == "boundary_sibling":
            score += 0.8
        score += family_fit * 1.4
        score += path_overlap * 0.6
        score += lexical_overlap * 0.75
        if score > best_score and (
            ownership_role in {"core_owner", "test_surface", "boundary_sibling"}
            or family_fit >= 0.8
            or path_overlap >= 1
            or lexical_overlap >= 2
            or score >= current_selected_score - 1.5
        ):
            best_owner = entry
            best_score = score
    if best_owner is None:
        return [dict(item) for item in selected_targets]
    rescued: List[Dict[str, Any]] = [dict(best_owner)]
    selected_paths = {str(best_owner.get("path") or "")}
    if (
        str(best_owner.get("candidate_role") or "") != "test_file"
        and _should_promote_paired_test_target(
            ranked_targets=ranked_targets,
            families=families,
            task_profile=task_profile,
        )
    ):
        paired_test = _pick_paired_test(str(best_owner.get("path") or ""), ranked, selected_paths)
        if paired_test is not None and len(rescued) < max(2, target_slot_limit):
            rescued.append(paired_test)
    return rescued[: max(1, target_slot_limit)]


def _drop_scheduler_targets_when_specific_owners_exist(
    *,
    selected_targets: Sequence[Mapping[str, Any]],
    families: Sequence[str],
) -> List[Dict[str, Any]]:
    if _is_scheduler_family(families):
        return [dict(item) for item in selected_targets]
    non_scheduler = [
        dict(item)
        for item in selected_targets
        if not (_is_scheduler_surface_path(str(item.get("path") or "").lower()) or Path(str(item.get("path") or "")).name.lower() == "test_scheduled_jobs.py")
    ]
    scheduler_targets = [
        dict(item)
        for item in selected_targets
        if _is_scheduler_surface_path(str(item.get("path") or "").lower()) or Path(str(item.get("path") or "")).name.lower() == "test_scheduled_jobs.py"
    ]
    if len(non_scheduler) >= 2:
        return non_scheduler
    if non_scheduler and scheduler_targets:
        best_non_scheduler = max(float(item.get("targetability_score") or item.get("score") or 0.0) for item in non_scheduler)
        best_scheduler = max(float(item.get("targetability_score") or item.get("score") or 0.0) for item in scheduler_targets)
        if best_non_scheduler >= best_scheduler - 1.2:
            return non_scheduler
    return [dict(item) for item in selected_targets]


def _fallback_promotable_target(
    candidates: Sequence[Mapping[str, Any]],
    *,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    ordered = sorted(
        [item for item in candidates if str(item.get("candidate_role") or "") in {"implementation_code", "config_file"}],
        key=lambda item: (
            -max(
                float(item.get("targetability_score") or 0.0),
                float(item.get("reference_value_score") or item.get("score") or 0.0),
            ),
            -float(item.get("family_owner_fit") or 0.0),
            str(item.get("path") or ""),
        ),
    )
    for item in ordered:
        path = str(item.get("path") or "")
        ownership_role = str(item.get("ownership_role") or "")
        passive_penalty = float(item.get("passive_relevance_penalty") or 0.0)
        superficial_penalty = float(item.get("superficial_match_penalty") or 0.0)
        targetability_score = float(item.get("targetability_score") or 0.0)
        reference_value_score = float(item.get("reference_value_score") or item.get("score") or 0.0)
        coverage_detail = _coerce_mapping(item.get("coverage_detail"))
        has_meaningful_coverage = bool(
            list(coverage_detail.get("matched_failure_categories") or [])
            or list(coverage_detail.get("matched_cross_cutting_flags") or [])
            or list(coverage_detail.get("matched_invariant_buckets") or [])
        )
        if ownership_role == "local_contract_doc":
            continue
        if _is_forbidden_implementation_target(path):
            continue
        if _is_truth_contract_family(families, task_profile) and str(path or "").lower().startswith("infra/pipeline/"):
            continue
        if not _is_scheduler_family(families) and _is_scheduler_surface_path(path):
            continue
        if passive_penalty >= 1.45 and ownership_role in {"projection", "read_only_route", "hub"} and not _is_ui_truth_projection_family(families, task_profile):
            continue
        if (
            superficial_penalty >= 2.6
            and not has_meaningful_coverage
            and float(item.get("family_owner_fit") or 0.0) < 0.9
            and targetability_score <= reference_value_score + 1.5
        ):
            continue
        promoted = dict(item)
        promoted["selection_lane"] = "implementation_target"
        promoted["edit_allowed"] = True
        promoted["score"] = round(max(float(promoted.get("targetability_score") or 0.0), float(promoted.get("score") or 0.0)), 4)
        promoted["reasons"] = _dedupe_strings(
            list(promoted.get("reasons") or [])
            + ["retained as fallback implementation target when aggressive filtering removed all edit candidates"]
        )
        return promoted
    return None


def _target_centrality_score(
    candidate: Mapping[str, Any],
    *,
    families: Sequence[str],
    subsystem_hypotheses: Sequence[str],
    candidate_subsystems: Sequence[str],
    filename_alias_index: Mapping[str, Sequence[str]],
    covers: Set[str],
    candidate_role: str,
    graph_score: float,
    lexical_overlap: int,
    matched_symbols: Set[str],
    explicit_path_request: bool,
    hub_kind: str,
    owner_cover_count: int,
    superficial_penalty: float,
    recovery_focus: bool,
    operator_focus: bool,
    validation_focus: bool,
) -> Tuple[float, List[str]]:
    path = str(candidate.get("path") or "")
    basename = Path(path).name.lower()
    active_subsystems = [str(item or "").strip() for item in subsystem_hypotheses if str(item or "").strip()]
    primary_subsystems = active_subsystems[:3]
    alignment_hits = [subsystem for subsystem in candidate_subsystems if subsystem in active_subsystems]
    alias_hits = [subsystem for subsystem in filename_alias_index.get(basename, []) if subsystem in active_subsystems]
    evidence_sources = {str(item or "").strip().lower() for item in list(candidate.get("evidence_sources") or []) if str(item or "").strip()}
    raw_lexical_score = float(_coerce_mapping(candidate.get("scores")).get("lexical_score") or 0.0)

    score = 0.0
    reasons: List[str] = []

    if alignment_hits:
        score += 0.55 + 0.28 * min(2, len(alignment_hits))
        if candidate_subsystems and candidate_subsystems[0] in primary_subsystems[:2]:
            score += 0.75
            reasons.append("aligned with primary subsystem")
        elif candidate_subsystems and candidate_subsystems[0] in primary_subsystems:
            score += 0.4
            reasons.append("aligned with active subsystem")

    if alias_hits:
        score += 0.85
        reasons.append("identified as subsystem entrypoint")

    responsibility_score = 0.0
    if owner_cover_count:
        responsibility_score += min(1.35, 0.42 * owner_cover_count)
    if recovery_focus:
        if "recovery_responsibility" in covers:
            responsibility_score += 1.05
        elif "recovery_path" in covers:
            responsibility_score += 0.45
    if operator_focus and "operator_flow" in covers:
        responsibility_score += 0.55
    if validation_focus and "validation_path" in covers:
        responsibility_score += 0.45
    if _is_scheduler_family(families) and "dispatch_path" in covers:
        responsibility_score += 0.45
    if matched_symbols:
        responsibility_score += 0.35
    if explicit_path_request:
        responsibility_score += 0.25
    if responsibility_score:
        score += responsibility_score
        if responsibility_score >= 0.8:
            reasons.append("directly owns task-facing responsibility")

    structural_score = 0.0
    if graph_score >= 0.8:
        structural_score += min(0.8, graph_score * 0.35)
    if candidate_role == "implementation_code" and (alias_hits or owner_cover_count > 0):
        structural_score += 0.25
    if structural_score:
        score += structural_score
        if structural_score >= 0.5:
            reasons.append("structurally central within subsystem")

    if hub_kind and owner_cover_count == 0 and not alias_hits:
        score -= 0.45

    if superficial_penalty > 0:
        score -= min(0.75, superficial_penalty * 0.6)

    thin_binding_like = any(token in basename for token in CENTRALITY_DEMOTION_TOKENS) and owner_cover_count == 0 and graph_score < 0.9
    if thin_binding_like:
        score -= 0.95
        reasons.append("demoted thin wrapper surface")

    plumbing_like = any(token in basename for token in PLUMBING_SURFACE_TOKENS) and owner_cover_count == 0 and not alias_hits and not explicit_path_request
    if plumbing_like:
        score -= 0.85
        reasons.append("demoted execution plumbing surface")

    semantic_only = "semantic" in evidence_sources and "deterministic" not in evidence_sources
    weak_lexical_support = lexical_overlap <= 1 and raw_lexical_score < 1.35 and not matched_symbols and not explicit_path_request
    if semantic_only and weak_lexical_support:
        score -= 1.15
        reasons.append("penalized conceptual-only weak lexical support")

    return round(max(-2.0, min(3.25, score)), 4), _dedupe_strings(reasons)


def _paired_name_signature(path: str) -> Set[str]:
    stop = {
        "server", "test", "tests", "lab", "assistant", "webui", "tools", "control", "plane", "python",
    }
    tokens = {
        token.lower()
        for token in re.split(r"[^A-Za-z0-9]+", Path(str(path or "")).stem.lower())
        if len(token) > 2 and token.lower() not in stop
    }
    return tokens


def _mailbox_writeback_surface_category(path: str, *, candidate_role: str = "") -> str:
    rel = str(path or "").replace("\\", "/").lower()
    basename = Path(rel).name.lower()
    if not rel:
        return ""
    if rel.startswith("infra/webui/server_lab_assistant_"):
        if "mailbox" in basename:
            return "mailbox_owner"
        if "job_lifecycle" in basename or basename == "server_lab_assistant_lifecycle.py":
            return "lifecycle_owner"
        if "writeback" in basename:
            return "writeback_owner"
        if any(
            token in basename
            for token in [
                "task_register",
                "execution_truth_ownership",
                "execution_reconciliation",
                "recovery",
                "routing_policy",
                "delegation_bindings",
            ]
        ):
            return "adjacent_truth_surface"
    if candidate_role == "test_file" or rel.startswith("tests/"):
        if "mailbox" in basename:
            return "mailbox_test"
        if "job_lifecycle" in basename or basename == "test_lab_assistant_lifecycle.py":
            return "lifecycle_test"
        if "writeback" in basename:
            return "writeback_test"
    if _is_scheduler_surface_path(rel) or basename == "scheduled_jobs.py":
        return "scheduler_surface"
    return ""


def _mailbox_writeback_entry_score(item: Mapping[str, Any]) -> float:
    return float(item.get("targetability_score") or item.get("reference_value_score") or item.get("score") or 0.0)


def _sort_mailbox_writeback_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    target_entries: bool,
) -> List[Dict[str, Any]]:
    ordered_entries = [dict(item) for item in entries]
    if not ordered_entries:
        return ordered_entries
    category_priority = {
        "mailbox_owner": 0,
        "lifecycle_owner": 1,
        "writeback_owner": 2,
    } if target_entries else {
        "mailbox_test": 0,
        "lifecycle_test": 1,
        "writeback_test": 2,
    }
    return sorted(
        ordered_entries,
        key=lambda item: (
            category_priority.get(
                _mailbox_writeback_surface_category(
                    str(item.get("path") or ""),
                    candidate_role=str(item.get("candidate_role") or ""),
                ),
                99,
            ),
            0 if str(item.get("ownership_role") or "") == "core_owner" else 1,
            -_mailbox_writeback_entry_score(item),
            str(item.get("path") or ""),
        ),
    )


def _ensure_mailbox_writeback_bundle_shape(
    *,
    selected_entries: Sequence[Mapping[str, Any]],
    candidate_pool: Sequence[Mapping[str, Any]],
    max_bundle_files: int,
    mailbox_writeback_seam_focus: bool,
) -> List[Dict[str, Any]]:
    ordered_entries = [dict(item) for item in selected_entries]
    if not mailbox_writeback_seam_focus or not ordered_entries:
        return ordered_entries[:max_bundle_files]

    owner_categories = ["mailbox_owner", "lifecycle_owner", "writeback_owner"]
    reference_categories = ["mailbox_test", "lifecycle_test", "writeback_test"]
    ordered_categories = owner_categories + reference_categories
    pool = _dedupe_ranked_entries_by_path(list(selected_entries) + list(candidate_pool))
    chosen: List[Dict[str, Any]] = []
    chosen_paths: Set[str] = set()

    for category in ordered_categories:
        matching = [
            dict(item)
            for item in pool
            if _mailbox_writeback_surface_category(
                str(item.get("path") or ""),
                candidate_role=str(item.get("candidate_role") or ""),
            ) == category
        ]
        if not matching:
            continue
        ordered_matching = _sort_mailbox_writeback_entries(
            matching,
            target_entries=category in owner_categories,
        )
        candidate = dict(ordered_matching[0])
        candidate_path = str(candidate.get("path") or "")
        if not candidate_path or candidate_path in chosen_paths:
            continue
        if category in owner_categories:
            candidate["selection_lane"] = "implementation_target"
            candidate["edit_allowed"] = True
        else:
            candidate["selection_lane"] = "reference_context"
            candidate["edit_allowed"] = False
        chosen.append(candidate)
        chosen_paths.add(candidate_path)

    for item in ordered_entries:
        if len(chosen) >= max_bundle_files:
            break
        item_path = str(item.get("path") or "")
        if not item_path or item_path in chosen_paths:
            continue
        chosen.append(dict(item))
        chosen_paths.add(item_path)

    return chosen[:max_bundle_files]


def _ownership_role_for_candidate(
    *,
    path: str,
    candidate_role: str,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
    mailbox_writeback_seam_focus: bool,
    candidate_subsystems: Sequence[str],
    covers: Set[str],
    owner_cover_count: int,    graph_score: float,
) -> str:
    basename = Path(str(path or "")).name.lower()
    rel = str(path or "").lower()
    subsystems = {str(item or "").strip() for item in candidate_subsystems if str(item or "").strip()}
    basename_tokens = {token for token in re.split(r"[^A-Za-z0-9]+", basename) if token}

    truth_contract_focus = _is_truth_contract_family(families, task_profile)
    orchestration_focus = _is_orchestration_checkpoint_family(families, task_profile)
    ui_projection_focus = _is_ui_truth_projection_family(families, task_profile)
    retrieval_ranking_focus = _is_retrieval_ranking_family(families, task_profile)
    scheduler_focus = _is_scheduler_family(families)
    seam_surface = _mailbox_writeback_surface_category(path, candidate_role=candidate_role)

    if candidate_role in {"documentation", "governance_doc"}:
        if truth_contract_focus and "contract" in basename and TRUTH_CONTRACT_DOC_TOKENS.intersection(basename_tokens):
            return "local_contract_doc"
        return "reference_doc"
    if candidate_role == "test_file":
        if mailbox_writeback_seam_focus and seam_surface in {"mailbox_test", "lifecycle_test", "writeback_test"}:
            return "test_surface"
        if truth_contract_focus and {"identity", "truth", "state"}.intersection(basename_tokens):
            return "test_surface"
        if orchestration_focus and any(token in rel for token in ["orchestrator", "workflow", "checkpoint", "turn", "builder", "verifier"]):            return "test_surface"
        if ui_projection_focus and any(token in rel for token in ["snapshot", "projection", "status", "health", "degraded", "unreachable"]):
            return "test_surface"
        if retrieval_ranking_focus and rel.startswith("tests/pipeline/"):
            return "test_surface"
        if scheduler_focus and any(token in rel for token in ["scheduler", "scheduled_jobs", "watchdog", "claim", "healer"]):
            return "test_surface"
        return "reference_test"
    if candidate_role not in {"implementation_code", "config_file"}:
        return ""

    if mailbox_writeback_seam_focus:
        if seam_surface in {"mailbox_owner", "lifecycle_owner", "writeback_owner"}:
            return "core_owner"
        if seam_surface == "adjacent_truth_surface":
            return "boundary_sibling"
        if seam_surface == "scheduler_surface":
            return "hub"

    if truth_contract_focus:
        if rel.startswith("infra/pipeline/"):            return ""
        if basename == "server_routes_and_main.py" or any(token in basename for token in TRUTH_CONTRACT_ROUTE_TOKENS):
            return "read_only_route"
        if any(token in basename for token in TRUTH_CONTRACT_PROJECTION_TOKENS):
            return "projection"
        if any(
            token in rel
            for token in [
                "execution_truth_ownership",
                "task_register",
                "delegation",
                "routing_policy",
                "server_ledger_actions",
                "ledger_schema",
                "operator_snapshot",
                "execution_reconciliation",
            ]
        ) and (owner_cover_count > 0 or graph_score >= 0.35):
            return "core_owner"
        if any(token in basename for token in TRUTH_CONTRACT_BOUNDARY_TOKENS):
            return "boundary_sibling"
        if owner_cover_count > 0 and subsystems.intersection(TRUTH_CONTRACT_OWNER_SUBSYSTEMS):
            return "core_owner"
        if ("state" in basename or "identity" in basename) and (graph_score >= 0.6 or subsystems.intersection(TRUTH_CONTRACT_OWNER_SUBSYSTEMS)):
            return "core_owner"
        if subsystems.intersection(TRUTH_CONTRACT_OWNER_SUBSYSTEMS):
            return "boundary_sibling"
        return ""

    if orchestration_focus:
        if rel.startswith("infra/bin/scheduler_") or basename in {"scheduler_watchdog.py", "scheduler_pipeline_healer.py", "scheduler_recovery_assistant.py"}:
            return "boundary_sibling"
        if basename in {"server_routes_and_main.py", "server_agents.py"} or rel.endswith("static/agents.js"):
            return "hub"
        if basename == "server_agents_orchestrator.py":
            return "core_owner"
        if any(token in rel for token in ["server_agents_orchestrator", "workflow", "checkpoint", "turn", "builder", "verifier", "orchestrator"]):
            if owner_cover_count > 0 or graph_score >= 0.35 or subsystems.intersection(ORCHESTRATION_OWNER_SUBSYSTEMS):
                return "core_owner"
        if subsystems.intersection(ORCHESTRATION_OWNER_SUBSYSTEMS) and (owner_cover_count > 0 or graph_score >= 0.5):
            return "core_owner"
        if any(token in rel for token in ORCHESTRATION_BOUNDARY_TOKENS):
            return "boundary_sibling"
        return ""

    if ui_projection_focus:
        if basename == "server_routes_and_main.py":
            return "read_only_route"
        if basename in {"server_agents.py", "scheduled_jobs.py"} or rel.endswith("static/agents.js"):
            return "hub"
        if rel.startswith("infra/webui/static/") and any(token in rel for token in ["snapshot", "projection", "status", "health", "reachability", "degraded", "unreachable", "reconnecting"]):
            return "boundary_sibling"
        if "operator_snapshot" in rel or any(token in rel for token in ["snapshot", "projection", "status", "health", "reachability", "degraded", "unreachable", "reconnecting"]):
            return "core_owner"
        if "task_register" in rel or any(token in rel for token in UI_PROJECTION_BOUNDARY_TOKENS):
            return "boundary_sibling"
        return ""

    if retrieval_ranking_focus:
        retrieval_owner_evidence = bool(
            "retrieval_owner" in covers
            or (
                subsystems.intersection({"retrieval_ranker", "answer_assembly"})
                and (owner_cover_count > 0 or graph_score >= 0.45)
            )
        )
        retrieval_adjacent_evidence = bool(
            "retrieval_scoring" in covers
            or "retrieval_search" in covers
            or "retrieval_answer" in covers
            or "retrieval_rules" in covers
            or subsystems.intersection({"query_shaping", "policy_assets"})
        )
        if rel.startswith("infra/pipeline/knowledge/"):
            return "boundary_sibling"
        if retrieval_owner_evidence:
            return "core_owner"
        if rel.endswith("infra/pipeline/planning.py") and (owner_cover_count > 0 or graph_score >= 0.65 or subsystems.intersection({"retrieval_ranker", "answer_assembly"})):
            return "core_owner"
        if ("retrieval_scoring" in covers or rel.endswith("infra/pipeline/stages/context_ranker.py")) and (retrieval_adjacent_evidence or subsystems.intersection({"retrieval_ranker"})):
            return "boundary_sibling"
        if ("retrieval_answer" in covers or rel.endswith("infra/pipeline/stages/repo_retriever.py")) and (retrieval_adjacent_evidence or subsystems.intersection({"answer_assembly"})):
            return "boundary_sibling"
        if (("retrieval_search" in covers or rel.endswith("infra/pipeline/stages/search_plan.py")) and (retrieval_adjacent_evidence or subsystems.intersection({"query_shaping"}))) or rel.endswith("infra/pipeline/semantic.py"):
            return "boundary_sibling"
        if ("retrieval_rules" in covers or rel.endswith("infra/pipeline/planning_ontology.py")) and (retrieval_adjacent_evidence or subsystems.intersection({"policy_assets"})):
            return "boundary_sibling"
        if rel.startswith("infra/pipeline/") and retrieval_adjacent_evidence:
            return "boundary_sibling"
        if rel.startswith("infra/bin/pipeline"):
            return "boundary_sibling"
        if rel.startswith("infra/controlplane/") or basename == "server_routes_and_main.py" or rel.startswith("infra/webui/"):
            return "hub"
        if any(token in rel for token in RETRIEVAL_RANKING_HUB_TOKENS):
            return "hub"
        return ""

    if scheduler_focus:
        if basename in {"server_routes_and_main.py", "server_agents.py"} or rel.endswith("static/agents.js"):
            return "hub"
        if basename == "scheduled_jobs.py":
            return "core_owner"
        if basename == "scheduler_retry_manager.py":
            return "core_owner"
        if rel.startswith("infra/bin/scheduler_") or basename.startswith("scheduler_") or any(token in rel for token in ["watchdog", "claim", "healer", "activation"]):
            return "boundary_sibling"
        return ""

    return ""


def _family_owner_fit(
    *,
    path: str,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
    mailbox_writeback_seam_focus: bool,
    ownership_role: str,
    covers: Set[str],
    candidate_subsystems: Sequence[str],) -> float:
    if ownership_role != "core_owner":
        return 0.0
    rel = str(path or "").lower()
    basename = Path(rel).name.lower()
    subsystems = {str(item or "").strip() for item in candidate_subsystems if str(item or "").strip()}
    score = 0.0
    seam_surface = _mailbox_writeback_surface_category(path)

    if mailbox_writeback_seam_focus:
        if seam_surface == "mailbox_owner":
            score += 2.55
        elif seam_surface == "lifecycle_owner":
            score += 2.8
        elif seam_surface == "writeback_owner":
            score += 2.7

    if _is_truth_contract_family(families, task_profile):
        if any(token in rel for token in ["assistant_state", "runtime_state", "truth", "identity", "execution_truth_ownership"]):            score += 2.4
        if any(
            token in rel
            for token in [
                "task_register",
                "delegation",
                "routing_policy",
                "server_ledger_actions",
                "ledger_schema",
                "operator_snapshot",
                "execution_reconciliation",
            ]
        ):
            score += 2.0
        if basename in {"server_agents_swarm.py", "server_agents.py"}:
            score -= 1.8
        if any(token in rel for token in ["swarm", "delegation", "snapshot", "turns"]):
            score -= 0.2
    elif _is_orchestration_checkpoint_family(families, task_profile):
        if basename == "server_agents_orchestrator.py":
            score += 2.8
        if rel.startswith("infra/bin/scheduler_"):
            score -= 2.0
        if any(token in rel for token in ["workflow", "checkpoint", "turn", "builder", "verifier"]):
            score += 0.95
    elif _is_ui_truth_projection_family(families, task_profile):
        if "operator_snapshot" in rel or any(token in rel for token in ["projection", "snapshot", "degraded", "unreachable", "reconnecting"]):
            score += 2.6
        if any(token in rel for token in ["delegation", "binding", "task_register", "scheduled_jobs", "static/agents.js", "server_agents"]):
            score -= 1.9
    elif _is_retrieval_ranking_family(families, task_profile):
        if "retrieval_owner" in covers:
            score += 2.25
        if subsystems.intersection({"retrieval_ranker"}):
            score += 1.1
        if subsystems.intersection({"answer_assembly"}):
            score += 0.75
        if "validation_path" in covers:
            score += 0.3
        if rel.endswith("infra/pipeline/planning.py"):
            score += 0.9
        elif rel.endswith("infra/pipeline/stages/context_ranker.py"):
            score -= 0.65
        elif rel.endswith("infra/pipeline/stages/repo_retriever.py"):
            score -= 0.55
        elif rel.endswith("infra/pipeline/stages/search_plan.py"):
            score -= 0.7
        elif rel.endswith("infra/pipeline/semantic.py"):
            score -= 0.4
        elif rel.startswith("infra/pipeline/"):
            score += 0.15
        if "retrieval_search" in covers or "retrieval_scoring" in covers or "retrieval_answer" in covers:
            score -= 0.45
        if "retrieval_rules" in covers and "retrieval_owner" not in covers:
            score -= 0.3
        if rel.startswith("infra/webui/") or rel.startswith("infra/controlplane/") or any(token in rel for token in ["task_register", "operator_snapshot", "mailbox", "ledger_manager", "apply_patch", "job_registry", "execution_reconciliation", "execution_truth_ownership", "writeback"]):
            score -= 2.8
    elif _is_scheduler_family(families):
        if basename == "scheduled_jobs.py":
            score += 2.8
        elif basename == "scheduler_retry_manager.py":
            score += 1.6
        if rel.startswith("infra/bin/scheduler_") or any(token in rel for token in ["watchdog", "healer", "recovery_assistant"]):
            score -= 1.25

    return round(score, 4)


def _pick_paired_test(owner_path: str, pool: Sequence[Mapping[str, Any]], selected_paths: Set[str]) -> Optional[Dict[str, Any]]:
    owner_sig = _paired_name_signature(owner_path)
    if not owner_sig:
        owner_sig = {Path(owner_path).stem.lower()}
    owner_lower = str(owner_path or "").lower()
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    for raw in pool:
        entry = dict(raw)
        path = str(entry.get("path") or "")
        if not path or path in selected_paths or str(entry.get("candidate_role") or "") != "test_file":
            continue
        path_lower = path.lower()
        overlap = owner_sig.intersection(_paired_name_signature(path))
        forced_pipeline_pair = False
        preferred_pipeline_pair = False
        if owner_lower.endswith("infra/pipeline/planning.py") and path_lower.endswith("tests/pipeline/test_planning_flow.py"):
            forced_pipeline_pair = True
            preferred_pipeline_pair = True
            overlap = overlap or {"planning"}
        elif owner_lower.endswith("infra/pipeline/stages/search_plan.py") and path_lower.endswith("tests/pipeline/test_stage3_search_plan_engine.py"):
            forced_pipeline_pair = True
            preferred_pipeline_pair = True
            overlap = overlap or {"search_plan"}
        elif owner_lower.endswith("infra/pipeline/stages/repo_retriever.py") and path_lower.endswith("tests/pipeline/test_planning_flow.py"):
            forced_pipeline_pair = True
            preferred_pipeline_pair = True
            overlap = overlap or {"repo_retriever"}
        elif owner_lower.startswith("infra/pipeline/") and path_lower in RETRIEVAL_RANKING_TEST_PATH_HINTS:
            forced_pipeline_pair = True
            overlap = overlap or {"pipeline"}
        if not overlap and not forced_pipeline_pair and not any(token in path_lower for token in owner_sig):
            continue
        score = float(entry.get("targetability_score") or entry.get("score") or 0.0)
        score += 1.8 * len(overlap)
        if forced_pipeline_pair:
            score += 3.2
        if preferred_pipeline_pair:
            score += 2.4
        if "state" in Path(path).name.lower():
            score += 0.6
        if owner_lower.startswith("infra/pipeline/") and path_lower.startswith("tests/pipeline/"):
            score += 0.8
        if best is None or score > best[0]:
            best = (score, entry)
    if best is None:
        return None
    out = dict(best[1])
    out["selection_lane"] = "implementation_target"
    out["edit_allowed"] = True
    out["ownership_role"] = "test_surface"
    out["reasons"] = _dedupe_strings(list(out.get("reasons") or []) + ["paired with dominant owner surface"])
    return out


def _should_promote_paired_test_target(
    *,
    ranked_targets: Sequence[Mapping[str, Any]],
    families: Sequence[str],
    task_profile: Mapping[str, Any],
) -> bool:
    if not _supports_paired_test_editing(families, task_profile):
        return False
    if _is_retrieval_ranking_family(families, task_profile):
        core_owner_paths = {
            str(item.get("path") or "")
            for item in ranked_targets
            if str(item.get("ownership_role") or "") == "core_owner"
            and str(item.get("candidate_role") or "") in {"implementation_code", "config_file"}
            and str(item.get("path") or "").startswith("infra/pipeline/")
        }
        if len(core_owner_paths) > 1:
            return False
    return True


def _dedupe_ranked_entries_by_path(entries: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for entry in entries:
        path = str(entry.get("path") or "")
        if not path or path in seen:
            continue
        out.append(dict(entry))
        seen.add(path)
    return out


def _is_validation_rank_candidate(entry: Mapping[str, Any]) -> bool:
    candidate_role = str(entry.get("candidate_role") or "")
    ownership_role = str(entry.get("ownership_role") or "")
    bundle_role = str(entry.get("bundle_role") or entry.get("role") or "")
    context_intent_type = str(entry.get("context_intent_type") or "")
    if ownership_role == "local_contract_doc" or candidate_role in {"documentation", "governance_doc"}:
        return False
    return bool(
        candidate_role == "test_file"
        or ownership_role == "test_surface"
        or bundle_role in {"paired_test", "validation_target", "validation_context", "witness_only"}
        or context_intent_type in {"validation_context", "witness_test"}
    )


def _select_additional_validation_targets(
    *,
    selected_targets: Sequence[Mapping[str, Any]],
    selected_refs: Sequence[Mapping[str, Any]],
    ranked_targets: Sequence[Mapping[str, Any]],
    ranked_refs: Sequence[Mapping[str, Any]],
    ranked: Sequence[Mapping[str, Any]],
    job_contract: Mapping[str, Any],
    task_profile: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    selected_paths = {
        str(item.get("path") or "")
        for item in list(selected_targets) + list(selected_refs)
        if str(item.get("path") or "")
    }
    existing_validation_paths = {
        str(item.get("path") or "")
        for item in list(selected_targets) + list(selected_refs)
        if _is_validation_rank_candidate(item)
    }
    archetype = _task_archetype(task_profile)
    explicit_test_request = _objective_requests_test_edits(job_contract)
    wants_validation = bool(
        explicit_test_request
        or archetype in {"implementation_change", "verification_test"}
        or any(_is_owner_modify_candidate(item) for item in selected_targets)
    )
    desired_count = 0
    if wants_validation:
        desired_count = 1
    if explicit_test_request or archetype == "verification_test":
        desired_count = 2
    additional: List[Dict[str, Any]] = []
    if not wants_validation or len(existing_validation_paths) >= desired_count:
        return additional

    claimed_paths = set(selected_paths)
    for owner in selected_targets:
        owner_path = str(owner.get("path") or "")
        if not owner_path or _is_validation_rank_candidate(owner):
            continue
        paired = _pick_paired_test(owner_path, ranked, claimed_paths)
        if paired is None:
            continue
        additional.append(paired)
        claimed_paths.add(str(paired.get("path") or ""))
        if len(existing_validation_paths) + len(additional) >= desired_count:
            return _dedupe_ranked_entries_by_path(additional)

    candidate_pool = sorted(
        [
            dict(item)
            for item in list(ranked_refs) + list(ranked_targets) + list(ranked)
            if _is_validation_rank_candidate(item) and str(item.get("path") or "") not in claimed_paths
        ],
        key=lambda item: (
            -max(
                float(item.get("reference_value_score") or 0.0),
                float(item.get("targetability_score") or 0.0),
                float(item.get("score") or 0.0),
            ),
            str(item.get("path") or ""),
        ),
    )
    for item in candidate_pool:
        additional.append(dict(item))
        claimed_paths.add(str(item.get("path") or ""))
        if len(existing_validation_paths) + len(additional) >= desired_count:
            break
    return _dedupe_ranked_entries_by_path(additional)


def _pick_local_contract_doc(query_terms: Set[str], pool: Sequence[Mapping[str, Any]], selected_paths: Set[str]) -> Optional[Dict[str, Any]]:
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    for raw in pool:
        entry = dict(raw)
        path = str(entry.get("path") or "")
        if not path or path in selected_paths:
            continue
        if str(entry.get("candidate_role") or "") not in {"documentation", "governance_doc"}:
            continue
        basename = Path(path).name.lower()
        if "contract" not in basename:
            continue
        score = float(entry.get("reference_value_score") or entry.get("score") or 0.0)
        basename_tokens = {token for token in re.split(r"[^A-Za-z0-9]+", basename) if token}
        score += 1.1 * len(basename_tokens.intersection({str(token or "").lower() for token in query_terms}))
        if basename.startswith("lab_assistant"):
            score += 1.25
        if "identity" in basename or "truth" in basename or "state" in basename:
            score += 1.0
        if best is None or score > best[0]:
            best = (score, entry)
    if best is None:
        return None
    out = dict(best[1])
    out["selection_lane"] = "reference_context"
    out["edit_allowed"] = False
    out["ownership_role"] = out.get("ownership_role") or "local_contract_doc"
    out["reasons"] = _dedupe_strings(list(out.get("reasons") or []) + ["preferred local contract reference"])
    return out


def _family_surface_categories(entry: Mapping[str, Any], *, families: Sequence[str], task_profile: Mapping[str, Any]) -> Set[str]:
    path = str(entry.get("path") or "").lower()
    basename = Path(path).name.lower()
    candidate_role = str(entry.get("candidate_role") or "")
    ownership_role = str(entry.get("ownership_role") or "")
    covers = {str(item or "").strip().lower() for item in list(entry.get("covers") or []) if str(item or "").strip()}
    categories: Set[str] = set()

    if _is_scheduler_family(families):
        if candidate_role == "test_file" and ("scheduled_jobs" in path or "scheduler" in path):
            categories.add("scheduler_test")
        if basename == "scheduled_jobs.py" or (ownership_role == "core_owner" and _is_scheduler_surface_path(path)):
            categories.add("scheduler_owner")
        if path.startswith("infra/bin/scheduler_") or any(token in path for token in ["watchdog", "healer", "recovery_assistant", "retry_manager"]):
            categories.add("scheduler_recovery")
        if (
            any(token in path for token in ["scheduledjobsscreen", "adminviewmodel", "adminapi", "chatviewmodel"])
            or ("scheduled" in path and path.endswith(UI_STATIC_EXTENSIONS))
            or (path.startswith("infra/webui/static/") and any(token in path for token in ["scheduled", "scheduler", "status", "operator"]))
        ):
            categories.add("scheduler_ui")

    if _is_ui_truth_projection_family(families, task_profile):
        if candidate_role == "test_file" and any(token in path for token in ["snapshot", "projection", "status", "health"]):
            categories.add("ui_truth_test")
        if ownership_role == "core_owner" and any(token in path for token in ["snapshot", "projection", "status", "health"]):
            categories.add("ui_truth_backend")
        if (
            any(token in path for token in ["chatviewmodel", "scheduledjobsscreen", "agents.js", "status", "health"])
            or path.endswith(UI_STATIC_EXTENSIONS)
            or (path.startswith("infra/webui/static/") and any(token in path for token in ["status", "health", "projection", "snapshot", "degraded", "unreachable"]))
        ):
            categories.add("ui_truth_projection")

    if _is_truth_contract_family(families, task_profile):
        if candidate_role == "test_file" and any(token in path for token in ["task_register", "assistant", "truth", "state"]):
            categories.add("truth_test")
        if any(token in path for token in ["assistant_state", "runtime_state", "server_lab_assistant_state"]):
            categories.add("truth_state")
        if any(token in path for token in ["execution_truth_ownership", "truth_ownership"]) or "truth_boundary" in covers:
            categories.add("truth_ownership")
        if any(token in path for token in ["execution_reconciliation", "reconciliation", "task_register"]) or "reconciliation_path" in covers:
            categories.add("truth_reconciliation")
        if any(token in path for token in ["web_operations", "writeback", "server_agents"]) or "writeback_boundary" in covers:
            categories.add("truth_writeback")

    if _is_retrieval_ranking_family(families, task_profile):
        if candidate_role == "test_file" and path.startswith("tests/pipeline/"):
            categories.add("retrieval_test")
        if "retrieval_owner" in covers or path.endswith("infra/pipeline/planning.py") or (ownership_role == "core_owner" and path.startswith("infra/pipeline/")):
            categories.add("retrieval_owner")
        if "retrieval_answer" in covers or path.endswith("infra/pipeline/stages/repo_retriever.py"):
            categories.add("retrieval_answer")
        if "retrieval_scoring" in covers or path.endswith("infra/pipeline/stages/context_ranker.py"):
            categories.add("retrieval_scoring")
        if "retrieval_search" in covers or path.endswith("infra/pipeline/stages/search_plan.py"):
            categories.add("retrieval_search")
        if "retrieval_rules" in covers or path.endswith("infra/pipeline/planning_ontology.py"):
            categories.add("retrieval_rules")
    return categories


def _family_shape_requirements(families: Sequence[str], task_profile: Mapping[str, Any]) -> List[Tuple[str, str]]:
    if _is_scheduler_family(families):
        return [
            ("scheduler_owner", "target"),
            ("scheduler_recovery", "reference"),
            ("scheduler_ui", "reference"),
            ("scheduler_test", "target"),
        ]
    if _is_ui_truth_projection_family(families, task_profile):
        return [
            ("ui_truth_backend", "target"),
            ("ui_truth_projection", "reference"),
            ("ui_truth_test", "target"),
        ]
    if _is_truth_contract_family(families, task_profile):
        return [
            ("truth_state", "target"),
            ("truth_ownership", "reference"),
            ("truth_reconciliation", "reference"),
            ("truth_writeback", "reference"),
            ("truth_test", "target"),
        ]
    if _is_retrieval_ranking_family(families, task_profile):
        return [
            ("retrieval_owner", "target"),
            ("retrieval_scoring", "reference"),
            ("retrieval_search", "reference"),
            ("retrieval_answer", "reference"),
            ("retrieval_test", "target"),
        ]
    return []


def _family_shape_priority(entry: Mapping[str, Any], *, category: str) -> Tuple[float, float, float]:
    path = str(entry.get("path") or "").lower()
    target_score = float(entry.get("targetability_score") or entry.get("score") or 0.0)
    reference_score = float(entry.get("reference_value_score") or entry.get("score") or 0.0)
    owner_fit = float(entry.get("family_owner_fit") or 0.0)
    bonus = 0.0
    if category == "scheduler_recovery" and ("watchdog" in path or "healer" in path or "recovery_assistant" in path):
        bonus += 1.4
    elif category == "scheduler_ui" and ("scheduledjobsscreen" in path or "adminviewmodel" in path or "adminapi" in path):
        bonus += 1.1
    elif category == "truth_test" and "task_register" in path:
        bonus += 1.2
    elif category == "truth_writeback" and ("web_operations" in path or "writeback" in path):
        bonus += 1.2
    elif category == "retrieval_answer" and path.endswith("repo_retriever.py"):
        bonus += 1.5
    elif category == "retrieval_scoring" and path.endswith("context_ranker.py"):
        bonus += 1.6
    elif category == "retrieval_search" and path.endswith("search_plan.py"):
        bonus += 1.5
    return (bonus + owner_fit, target_score, reference_score)


def _evict_for_family_shape(
    entries: List[Dict[str, Any]],
    *,
    preferred_kind: str,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    if not entries:
        return None
    scored: List[Tuple[Tuple[int, int, float], Dict[str, Any]]] = []
    for entry in entries:
        categories = _family_surface_categories(entry, families=families, task_profile=task_profile)
        is_core_owner = str(entry.get("ownership_role") or "") == "core_owner"
        is_test = str(entry.get("candidate_role") or "") == "test_file"
        protected = bool(categories) or is_core_owner or (preferred_kind == "reference" and is_test)
        score = float(entry.get("reference_value_score") or entry.get("targetability_score") or entry.get("score") or 0.0)
        scored.append(((1 if protected else 0, 1 if is_test else 0, score), entry))
    scored.sort(key=lambda item: item[0])
    return dict(scored[0][1]) if scored else None


def _ensure_family_shape(
    *,
    selected_targets: List[Dict[str, Any]],
    selected_refs: List[Dict[str, Any]],
    ranked_targets: Sequence[Mapping[str, Any]],
    ranked_refs: Sequence[Mapping[str, Any]],
    max_selected_files: int,
    max_reference_keep: int,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    requirements = _family_shape_requirements(families, task_profile)
    if not requirements:
        return selected_targets, selected_refs

    selected_targets = [dict(item) for item in selected_targets]
    selected_refs = [dict(item) for item in selected_refs]
    selected_paths = {str(item.get("path") or "") for item in selected_targets + selected_refs}
    selected_categories = {
        category
        for item in selected_targets + selected_refs
        for category in _family_surface_categories(item, families=families, task_profile=task_profile)
    }
    ranked_pool = [dict(item) for item in list(ranked_targets) + list(ranked_refs)]

    for category, preferred_kind in requirements:
        if category in selected_categories:
            continue
        candidates = [
            item for item in ranked_pool
            if str(item.get("path") or "") not in selected_paths
            and category in _family_surface_categories(item, families=families, task_profile=task_profile)
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda item: _family_shape_priority(item, category=category), reverse=True)
        candidate = dict(candidates[0])
        candidate_path = str(candidate.get("path") or "")
        target_bucket = selected_targets if preferred_kind == "target" and str(candidate.get("selection_lane") or "") == "implementation_target" else selected_refs

        if target_bucket is selected_refs and len(selected_refs) >= max_reference_keep:
            removed = _evict_for_family_shape(selected_refs, preferred_kind="reference", families=families, task_profile=task_profile)
            if removed is not None:
                selected_refs = [item for item in selected_refs if str(item.get("path") or "") != str(removed.get("path") or "")]
                selected_paths.discard(str(removed.get("path") or ""))
        elif len(selected_targets) + len(selected_refs) >= max_selected_files:
            if selected_refs:
                removed = _evict_for_family_shape(selected_refs, preferred_kind="reference", families=families, task_profile=task_profile)
                if removed is not None:
                    selected_refs = [item for item in selected_refs if str(item.get("path") or "") != str(removed.get("path") or "")]
                    selected_paths.discard(str(removed.get("path") or ""))
            elif selected_targets:
                removed = _evict_for_family_shape(selected_targets, preferred_kind="target", families=families, task_profile=task_profile)
                if removed is not None and str(removed.get("ownership_role") or "") != "core_owner":
                    selected_targets = [item for item in selected_targets if str(item.get("path") or "") != str(removed.get("path") or "")]
                    selected_paths.discard(str(removed.get("path") or ""))

        if len(selected_targets) + len(selected_refs) >= max_selected_files and target_bucket is selected_refs and len(selected_refs) >= max_reference_keep:
            continue

        if target_bucket is selected_refs:
            candidate["selection_lane"] = "reference_context"
            candidate["edit_allowed"] = False
            candidate["reasons"] = _dedupe_strings(list(candidate.get("reasons") or []) + ["retained as family-specific adjacent context"])
        target_bucket.append(candidate)
        selected_paths.add(candidate_path)
        selected_categories.add(category)

    selected_refs.sort(
        key=lambda item: (
            0 if _family_surface_categories(item, families=families, task_profile=task_profile) else 1,
            -float(item.get("reference_value_score") or item.get("score") or 0.0),
            str(item.get("path") or ""),
        )
    )
    if _is_retrieval_ranking_family(families, task_profile):
        categorized_refs = [item for item in selected_refs if _family_surface_categories(item, families=families, task_profile=task_profile)]
        if categorized_refs:
            selected_refs = categorized_refs[:max_reference_keep]
    elif _is_ui_truth_projection_family(families, task_profile):
        categorized_refs = [item for item in selected_refs if _family_surface_categories(item, families=families, task_profile=task_profile).intersection({"ui_truth_projection"})]
        if categorized_refs:
            selected_refs = categorized_refs[:max_reference_keep]
    elif _is_truth_contract_family(families, task_profile):
        kept_refs = [
            item for item in selected_refs
            if _family_surface_categories(item, families=families, task_profile=task_profile).intersection(
                {"truth_test", "truth_ownership", "truth_reconciliation", "truth_writeback"}
            )
            or str(item.get("ownership_role") or "") == "local_contract_doc"
            or str(item.get("candidate_role") or "") in {"documentation", "governance_doc"}
        ]
        if kept_refs:
            selected_refs = kept_refs[:max_reference_keep]
    return selected_targets, selected_refs


def _ensure_mailbox_writeback_seam_targets(
    *,
    selected_targets: List[Dict[str, Any]],
    selected_refs: List[Dict[str, Any]],
    ranked_targets: Sequence[Mapping[str, Any]],
    ranked_refs: Sequence[Mapping[str, Any]],
    max_selected_files: int,
    mailbox_writeback_seam_focus: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not mailbox_writeback_seam_focus:
        return selected_targets, selected_refs

    required_categories = ["mailbox_owner", "lifecycle_owner", "writeback_owner"]
    required_reference_categories = ["mailbox_test", "lifecycle_test", "writeback_test"]
    category_priority = {
        "mailbox_owner": 0,
        "lifecycle_owner": 1,
        "writeback_owner": 2,
    }
    selected_targets = [dict(item) for item in selected_targets]
    selected_refs = [dict(item) for item in selected_refs]
    selected_paths = {str(item.get("path") or "") for item in selected_targets + selected_refs}

    def _category(item: Mapping[str, Any]) -> str:
        return _mailbox_writeback_surface_category(
            str(item.get("path") or ""),
            candidate_role=str(item.get("candidate_role") or ""),
        )

    def _score(item: Mapping[str, Any]) -> float:
        return _mailbox_writeback_entry_score(item)

    def _evict_for_room() -> None:
        nonlocal selected_targets, selected_refs, selected_paths
        if len(selected_targets) + len(selected_refs) < max_selected_files:
            return
        protected_categories = set(required_categories).union(required_reference_categories)
        removable_refs = [item for item in selected_refs if _category(item) not in protected_categories]
        if removable_refs:
            removable = min(removable_refs, key=_score)
            removable_path = str(removable.get("path") or "")
            selected_refs = [item for item in selected_refs if str(item.get("path") or "") != removable_path]
            selected_paths.discard(removable_path)
            return
        removable_targets = [item for item in selected_targets if _category(item) not in required_categories]
        if removable_targets:
            removable = min(removable_targets, key=_score)
            removable_path = str(removable.get("path") or "")
            selected_targets = [item for item in selected_targets if str(item.get("path") or "") != removable_path]
            selected_paths.discard(removable_path)

    for category in required_categories:
        current_target_categories = {_category(item) for item in selected_targets}
        if category in current_target_categories:
            continue

        promoted = next((item for item in selected_refs if _category(item) == category), None)
        if promoted is not None:
            promoted_path = str(promoted.get("path") or "")
            selected_refs = [item for item in selected_refs if str(item.get("path") or "") != promoted_path]
            promoted_item = dict(promoted)
            promoted_item["selection_lane"] = "implementation_target"
            promoted_item["edit_allowed"] = True
            promoted_item["reasons"] = _dedupe_strings(list(promoted_item.get("reasons") or []) + ["promoted to preserve mailbox→lifecycle→writeback seam coverage"])
            selected_targets.append(promoted_item)
            continue

        candidates = [
            dict(item)
            for item in list(ranked_targets) + list(ranked_refs)
            if str(item.get("path") or "") not in selected_paths and _category(item) == category
        ]
        if not candidates:
            continue
        candidates.sort(key=_score, reverse=True)
        _evict_for_room()
        if len(selected_targets) + len(selected_refs) >= max_selected_files:
            continue
        candidate = dict(candidates[0])
        candidate_path = str(candidate.get("path") or "")
        candidate["selection_lane"] = "implementation_target"
        candidate["edit_allowed"] = True
        candidate["reasons"] = _dedupe_strings(list(candidate.get("reasons") or []) + ["retained as mailbox→lifecycle→writeback seam owner"])
        selected_targets.append(candidate)
        selected_paths.add(candidate_path)

    for category in required_reference_categories:
        current_reference_categories = {_category(item) for item in selected_refs}
        if category in current_reference_categories:
            continue
        candidates = [
            dict(item)
            for item in list(ranked_refs) + list(ranked_targets)
            if str(item.get("path") or "") not in selected_paths and _category(item) == category
        ]
        if not candidates:
            continue
        candidates.sort(key=_score, reverse=True)
        _evict_for_room()
        if len(selected_targets) + len(selected_refs) >= max_selected_files:
            continue
        candidate = dict(candidates[0])
        candidate_path = str(candidate.get("path") or "")
        candidate["selection_lane"] = "reference_context"
        candidate["edit_allowed"] = False
        candidate["reasons"] = _dedupe_strings(list(candidate.get("reasons") or []) + ["retained as mailbox→lifecycle→writeback seam regression witness"])
        selected_refs.append(candidate)
        selected_paths.add(candidate_path)

    selected_targets.sort(
        key=lambda item: (
            category_priority.get(_category(item), 99),
            0 if str(item.get("ownership_role") or "") == "core_owner" else 1,
            -_score(item),
            str(item.get("path") or ""),
        )
    )
    selected_refs.sort(
        key=lambda item: (
            0 if _category(item) in required_reference_categories else 1,
            -_score(item),
            str(item.get("path") or ""),
        )
    )
    return selected_targets, selected_refs

def _ensure_retrieval_family_refs(
    *,
    selected_targets: List[Dict[str, Any]],
    selected_refs: List[Dict[str, Any]],
    ranked_targets: Sequence[Mapping[str, Any]],
    ranked_refs: Sequence[Mapping[str, Any]],
    max_selected_files: int,
    max_reference_keep: int,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not _is_retrieval_ranking_family(families, task_profile):
        return selected_targets, selected_refs

    selected_targets = [dict(item) for item in selected_targets]
    selected_refs = [dict(item) for item in selected_refs]
    selected_paths = {str(item.get("path") or "") for item in selected_targets + selected_refs}
    selected_categories = {
        category
        for item in selected_targets + selected_refs
        for category in _family_surface_categories(item, families=families, task_profile=task_profile)
    }
    ranked_pool = [dict(item) for item in list(ranked_targets) + list(ranked_refs)]

    for category in ["retrieval_search", "retrieval_answer", "retrieval_scoring"]:
        if category in selected_categories:
            continue
        candidates = [
            item for item in ranked_pool
            if str(item.get("path") or "") not in selected_paths
            and category in _family_surface_categories(item, families=families, task_profile=task_profile)
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda item: _family_shape_priority(item, category=category), reverse=True)
        candidate = dict(candidates[0])
        candidate_path = str(candidate.get("path") or "")
        if len(selected_refs) >= max_reference_keep:
            removed = _evict_for_family_shape(selected_refs, preferred_kind="reference", families=families, task_profile=task_profile)
            if removed is not None:
                selected_refs = [item for item in selected_refs if str(item.get("path") or "") != str(removed.get("path") or "")]
                selected_paths.discard(str(removed.get("path") or ""))
                selected_categories = {
                    kind
                    for item in selected_targets + selected_refs
                    for kind in _family_surface_categories(item, families=families, task_profile=task_profile)
                }
        if len(selected_targets) + len(selected_refs) >= max_selected_files and selected_refs:
            removed = _evict_for_family_shape(selected_refs, preferred_kind="reference", families=families, task_profile=task_profile)
            if removed is not None:
                selected_refs = [item for item in selected_refs if str(item.get("path") or "") != str(removed.get("path") or "")]
                selected_paths.discard(str(removed.get("path") or ""))
                selected_categories = {
                    kind
                    for item in selected_targets + selected_refs
                    for kind in _family_surface_categories(item, families=families, task_profile=task_profile)
                }
        if len(selected_targets) + len(selected_refs) >= max_selected_files:
            continue
        candidate["selection_lane"] = "reference_context"
        candidate["edit_allowed"] = False
        candidate["reasons"] = _dedupe_strings(list(candidate.get("reasons") or []) + ["retained as retrieval family adjacent context"])
        selected_refs.append(candidate)
        selected_paths.add(candidate_path)
        selected_categories.add(category)

    return selected_targets, selected_refs


def _primary_task_type(task_profile: Mapping[str, Any]) -> str:
    return str(task_profile.get("primary_task_type") or "").strip().lower()


def _is_docs_task(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    return "documentation_contracts" in set(families) or _primary_task_type(task_profile) == "docs"


def _prefers_docs_reference(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    if _is_docs_task(families, task_profile):
        return True
    primary = _primary_task_type(task_profile)
    if primary in {"design", "truth_contract"}:
        return True
    family_set = _family_alias_set(families)
    return bool(family_set.intersection(MEMORY_FOCUS_FAMILIES.union(TRUTH_CONTRACT_FAMILIES)))


def _family_alias_set(families: Sequence[str]) -> Set[str]:
    family_set = {str(item or "").strip() for item in families if str(item or "").strip()}
    if "scheduler_safety" in family_set:
        family_set.add("scheduler_hardening")
    if "reconciliation_recovery" in family_set:
        family_set.add("workflow_recovery")
    return family_set


def _is_scheduler_family(families: Sequence[str]) -> bool:
    family_set = _family_alias_set(families)
    return "scheduler_safety" in family_set or "scheduler_hardening" in family_set or "workflow_recovery" in family_set


def _is_lab_assistant_memory_family(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    family_set = _family_alias_set(families)
    return bool(family_set.intersection(MEMORY_FOCUS_FAMILIES))


def _is_truth_contract_family(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    family_set = _family_alias_set(families)
    if family_set.intersection(TRUTH_CONTRACT_FAMILIES):
        return True
    return _primary_task_type(task_profile) == "truth_contract"


def _is_orchestration_checkpoint_family(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    family_set = _family_alias_set(families)
    if family_set.intersection(ORCHESTRATION_CHECKPOINT_FAMILIES):
        return True
    primary = _primary_task_type(task_profile)
    return primary == "orchestration_checkpointing"


def _is_ui_truth_projection_family(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    family_set = _family_alias_set(families)
    if family_set.intersection(UI_TRUTH_PROJECTION_FAMILIES):
        return True
    primary = _primary_task_type(task_profile)
    return primary == "ui_truth_projection"


def _is_retrieval_ranking_family(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    family_set = _family_alias_set(families)
    if family_set.intersection(RETRIEVAL_RANKING_FAMILIES):
        return True
    primary = _primary_task_type(task_profile)
    return primary == "retrieval_ranking_contract"


def _supports_paired_test_editing(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    return bool(
        _is_test_first_task(families, task_profile)
        or _is_truth_contract_family(families, task_profile)
        or _is_orchestration_checkpoint_family(families, task_profile)
        or _is_ui_truth_projection_family(families, task_profile)
        or _is_retrieval_ranking_family(families, task_profile)
        or _is_scheduler_family(families)
    )


def _supports_owner_collapse(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    return bool(
        _is_truth_contract_family(families, task_profile)
        or _is_orchestration_checkpoint_family(families, task_profile)
        or _is_ui_truth_projection_family(families, task_profile)
        or _is_retrieval_ranking_family(families, task_profile)
        or _is_scheduler_family(families)
    )


def _effective_families(job_contract: Mapping[str, Any], task_profile: Mapping[str, Any]) -> List[str]:
    explicit = [str(item or "").strip() for item in list(task_profile.get("task_families") or []) if str(item or "").strip()]
    detected = detect_task_families(job_contract)
    primary = _primary_task_type(task_profile)
    primary_aliases = {
        "truth_contract": ["assistant_truth_and_identity"],
        "orchestration_checkpointing": ["orchestration_checkpointing"],
        "ui_truth_projection": ["ui_truth_projection"],
        "retrieval_ranking_contract": ["retrieval_ranking_contract"],
    }.get(primary, [])
    explicit_set = {item for item in explicit}
    focused_families = TRUTH_CONTRACT_FAMILIES.union(ORCHESTRATION_CHECKPOINT_FAMILIES).union(UI_TRUTH_PROJECTION_FAMILIES).union(RETRIEVAL_RANKING_FAMILIES)
    legacy_broad_families = {"workflow_recovery", "multi_agent_orchestration", "lab_assistant_memory", "generic", "unknown"}
    if explicit_set.intersection(focused_families):
        detected = [item for item in detected if item in explicit_set or item in primary_aliases]
    elif explicit_set and explicit_set.issubset(legacy_broad_families):
        detected = [item for item in detected if item in focused_families or item in explicit_set or item in primary_aliases]
    elif explicit_set and not explicit_set.issubset(legacy_broad_families):
        detected = [item for item in detected if item not in focused_families]
    return _dedupe_strings(list(explicit) + list(detected) + list(primary_aliases))


def _task_text_blob(job_contract: Mapping[str, Any]) -> str:
    return " ".join(
        [
            str(job_contract.get("title") or ""),
            str(job_contract.get("description") or ""),
            str(job_contract.get("objective") or ""),
            str(job_contract.get("issue_context", {}).get("user_notes") or ""),
        ]
    ).lower()


def _looks_narrow_task(job_contract: Mapping[str, Any], task_profile: Mapping[str, Any], families: Sequence[str]) -> bool:
    family_set = _family_alias_set(families)
    if family_set.intersection({"scheduler_hardening", "workflow_recovery", "tool_spec_implementation", "documentation_contracts", "deterministic_patch_helper"}):
        return False
    if _is_lab_assistant_memory_family(families, task_profile):
        return False
    expected_scope = str(task_profile.get("expected_edit_scope") or "").strip().lower()
    if expected_scope == "single_file":
        return True
    changed_files = [str(item or "").strip() for item in list(job_contract.get("issue_context", {}).get("changed_files_hint") or []) if str(item or "").strip()]
    text = _task_text_blob(job_contract)
    tokens = set(IDENT_RE.findall(text))
    noun_hits = {token for token in tokens if token in NARROW_TASK_NOUN_MARKERS}
    verb_hits = {token for token in tokens if token in NARROW_TASK_VERB_MARKERS}
    explicit_narrow_phrase = any(marker in text for marker in ["mobile view", "too wide", "start button", "validation rule", "one endpoint", "one checkbox", "one page", "one panel"])
    broad_system_terms = {"architecture", "system", "pipeline", "scheduler", "workflow", "taxonomy", "refactor", "program"}
    broad_system_hits = tokens.intersection(broad_system_terms)
    narrow_shape = bool(explicit_narrow_phrase or (noun_hits and verb_hits) or (len(changed_files) <= 1 and (noun_hits or verb_hits)))
    if len(broad_system_hits) >= 2:
        return False
    if broad_system_hits and not (narrow_shape and len(changed_files) <= 1):
        return False
    return narrow_shape


def _core_hub_kind(path: str) -> str:
    basename = Path(str(path or "")).name.lower()
    return str(CORE_HUB_PATH_MARKERS.get(basename) or "")


def _path_keyword_overlap(path: str, query_terms: Set[str]) -> Set[str]:
    stem_tokens = {
        token.lower()
        for token in re.split(r"[^A-Za-z0-9]+", Path(str(path or "")).stem.lower())
        if len(token) > 4 and token.lower() not in CORE_HUB_GENERIC_TOKENS
    }
    return stem_tokens.intersection({str(token or "").lower() for token in query_terms})


def _is_test_first_task(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    primary = _primary_task_type(task_profile)
    return primary == "test_fix" or "testing_verification" in _family_alias_set(families)


def _is_config_task(families: Sequence[str], task_profile: Mapping[str, Any]) -> bool:
    primary = _primary_task_type(task_profile)
    return primary == "config" or "config_tuning" in set(families)


def _is_scheduler_surface_path(path: str) -> bool:
    rel = str(path or "").lower()
    return any(token in rel for token in SCHEDULER_SURFACE_HINTS)


def _is_tool_spec_surface_path(path: str) -> bool:
    rel = str(path or "").lower()
    return any(token in rel for token in TOOL_SPEC_SURFACE_HINTS)


def _forbidden_modify_path(path: str, *, families: Sequence[str], task_profile: Mapping[str, Any], is_global_replace: bool = False) -> bool:
    rel = str(path or "").lower().strip()
    if not rel:
        return True
    if rel.startswith(("tmp/", "runtime/", "governance/", "reports/")):
        return True
    if rel.endswith(NON_DOC_EXTENSIONS):
        return not _is_docs_task(families, task_profile)
    if rel.startswith("docs/"):
        return not _is_docs_task(families, task_profile)
    if "/prompts/" in rel:
        return not _is_docs_task(families, task_profile)
    return False


def _is_bundle_reference_only_path(path: str) -> bool:
    rel = str(path or "").lower()
    return rel.startswith(("docs/", "runtime/", "tmp/", "governance/", "reports/")) or rel.endswith(NON_DOC_EXTENSIONS)


def _semantic_target_weight(candidate_role: str, *, families: Sequence[str], task_profile: Mapping[str, Any]) -> float:
    role = str(candidate_role or "")
    if role == "implementation_code":
        return 1.0
    if role == "config_file":
        return 0.55 if _is_config_task(families, task_profile) else 0.3
    if role == "test_file":
        if _is_truth_contract_family(families, task_profile):
            return 0.42
        return 0.45 if _is_test_first_task(families, task_profile) else 0.15
    if role in {"documentation", "governance_doc", "temporary_artifact"}:
        return 0.2 if _is_docs_task(families, task_profile) else 0.0
    return 0.1


def _semantic_reference_weight(candidate_role: str, *, families: Sequence[str], task_profile: Mapping[str, Any]) -> float:
    role = str(candidate_role or "")
    if role == "implementation_code":
        return 0.25
    if role == "config_file":
        return 0.3
    if role == "test_file":
        return 0.25
    if role == "documentation":
        return 0.55
    if role == "governance_doc":
        return 0.45
    if role == "temporary_artifact":
        return 0.1
    return 0.2


def _reference_role_bonus(path: str, role: str, families: Sequence[str], task_profile: Mapping[str, Any]) -> float:
    rel = str(path or "").lower()
    basename = Path(rel).name.lower()
    bonus = 0.0
    if role in {"documentation", "governance_doc"}:
        bonus += 2.8 if _is_docs_task(families, task_profile) else (2.15 if _prefers_docs_reference(families, task_profile) else 1.5)
    elif role == "test_file":
        bonus += 1.0
    elif role == "config_file":
        bonus += 1.1
    elif role == "implementation_code":
        bonus += 0.5
    if _is_scheduler_family(families):
        if _is_scheduler_surface_path(rel):
            bonus += 1.6
        elif role == "implementation_code" and rel.startswith("infra/pipeline/"):
            bonus += 0.25
    if _is_retrieval_ranking_family(families, task_profile):
        if role == "test_file" and rel.startswith("tests/pipeline/"):
            bonus += 0.8
        if rel.startswith("infra/controlplane/") or basename == "scheduled_jobs.py":
            bonus -= 2.4
        if rel.startswith("infra/pipeline/knowledge_drafts/") or rel.startswith("tests/benchmarks/pipeline/"):
            bonus -= 1.8
    if _is_non_implementation_governance_surface(rel) and not _is_docs_task(families, task_profile):
        bonus -= 6.5
    if not _is_scheduler_family(families) and (_is_scheduler_surface_path(rel) or basename == "test_scheduled_jobs.py"):
        bonus -= 4.8
    if _is_mobile_ui_path(rel) and "mobile_android_ui" not in set(families):
        bonus -= 2.8
        if _is_orchestration_checkpoint_family(families, task_profile) or _is_scheduler_family(families) or _is_retrieval_ranking_family(families, task_profile):
            bonus -= 1.6
        if _is_ui_truth_projection_family(families, task_profile) and not (
            rel.startswith("infra/webui/static/")
            or any(token in rel for token in ["projection", "snapshot", "status", "health", "degraded", "unreachable", "reconnecting"])
        ):
            bonus -= 1.4
    if "tool_spec_implementation" in set(families) and _is_tool_spec_surface_path(rel):
        bonus += 2.0
    return bonus


def _should_skip_reference_candidate(
    path: str,
    *,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
    query_terms: Set[str],
) -> bool:
    rel = str(path or "").lower()
    basename = Path(rel).name.lower()
    if not rel:
        return True
    if _is_contamination_surface(rel):
        return True
    if _is_non_implementation_governance_surface(rel) and not _is_docs_task(families, task_profile):
        return True
    if (
        (
            rel.startswith("docs/90_archive/markdown_source/agentic-pipeline-v0.1/")
            or rel.startswith("docs/90_archive/markdown_source/persistent_files_chatgpt/")
            or rel.startswith("docs/contracts/")
        )
        and not _is_docs_task(families, task_profile)
        and not _is_truth_contract_family(families, task_profile)
        and not _is_retrieval_ranking_family(families, task_profile)
    ):
        return True
    if (
        _is_truth_contract_family(families, task_profile)
        and rel.startswith("infra/pipeline/")
        and not _is_retrieval_ranking_family(families, task_profile)
        and not any(token in rel for token in ["planning_ontology", "contracts"])
        and not any(token in query_terms for token in {"pipeline", "planning", "retrieval", "ranking"})
    ):
        return True
    if not _is_scheduler_family(families) and (_is_scheduler_surface_path(rel) or basename == "test_scheduled_jobs.py") and not _query_mentions_scheduler_surface(query_terms):
        return True
    if (
        _is_mobile_ui_path(rel)
        and "mobile_android_ui" not in set(families)
        and not _is_ui_truth_projection_family(families, task_profile)
        and not _query_mentions_mobile_surface(query_terms)
    ):
        return True
    return False


def _base_target_score(candidate: Mapping[str, Any], candidate_role: str) -> float:
    scores = dict(candidate.get("scores") or {})
    lexical = min(6.0, float(scores.get("lexical_score") or 0.0))
    structural = min(12.0, float(scores.get("structural_score") or 0.0))
    graph = min(4.0, float(scores.get("graph_score") or 0.0))
    role_multiplier = {
        "implementation_code": 1.0,
        "config_file": 0.8,
        "test_file": 0.55,
        "documentation": 0.22,
        "governance_doc": 0.15,
        "temporary_artifact": 0.05,
    }.get(str(candidate_role or ""), 0.35)
    return (lexical * 1.2 + structural * 0.9 + graph * 0.8) * role_multiplier


def _base_reference_score(candidate: Mapping[str, Any], candidate_role: str) -> float:
    scores = dict(candidate.get("scores") or {})
    lexical = min(6.0, float(scores.get("lexical_score") or 0.0))
    structural = min(10.0, float(scores.get("structural_score") or 0.0))
    graph = min(4.0, float(scores.get("graph_score") or 0.0))
    role_multiplier = {
        "implementation_code": 0.7,
        "config_file": 0.8,
        "test_file": 0.75,
        "documentation": 1.0,
        "governance_doc": 0.9,
        "temporary_artifact": 0.15,
    }.get(str(candidate_role or ""), 0.5)
    return (lexical * 1.0 + structural * 0.7 + graph * 0.6) * role_multiplier


def detect_task_families(job_contract: Mapping[str, Any]) -> List[str]:
    text = " ".join([
        str(job_contract.get("title") or ""),
        str(job_contract.get("description") or ""),
        str(job_contract.get("objective") or ""),
        " ".join([str(x) for x in job_contract.get("constraints", [])]),
        str(job_contract.get("issue_context", {}).get("user_notes") or ""),
    ]).lower()
    families: List[str] = []
    for family, patterns in TASK_FAMILY_PATTERNS.items():
        if any(re.search(p, text) for p in patterns):
            families.append(family)
    return families

def _word_set(value: str) -> set[str]:
    return {token.lower() for token in IDENT_RE.findall(str(value or "")) if len(token) > 2}



def _looks_non_implementation_doc(path: str) -> bool:
    rel = str(path or "").lower()
    if rel.startswith("tmp/"):
        return True
    if rel.startswith("governance/"):
        return True
    if rel.startswith("infra/pipeline/knowledge_drafts/") or rel.startswith("tests/benchmarks/pipeline/"):
        return True
    if rel.endswith((".md", ".txt", ".rst")):
        return True
    if "/prompts/" in rel:
        return True
    return False

def _looks_probable_code_target(path: str) -> bool:
    rel = str(path or "").lower()
    return rel.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".kt", ".rs", ".sh", ".ps1", ".yaml", ".yml", ".toml"))


def _is_mobile_ui_path(path: str) -> bool:
    rel = str(path or "").lower()
    return any(token in rel for token in MOBILE_UI_PATH_TERMS)


def _query_mentions_mobile_surface(query_terms: Set[str]) -> bool:
    lowered = {str(token or "").lower() for token in query_terms}
    return bool(lowered.intersection(MOBILE_QUERY_TOKENS))


def _query_mentions_scheduler_surface(query_terms: Set[str]) -> bool:
    lowered = {str(token or "").lower() for token in query_terms}
    return bool(lowered.intersection(SCHEDULER_QUERY_TOKENS))




def _classify_candidate_role(path: str, families: List[str], task_profile: Mapping[str, Any]) -> str:
    rel = str(path or "").lower()
    if rel.startswith("tmp/") or rel.startswith("runtime/") or rel.startswith("jobs/"):
        return "temporary_artifact"
    if rel.startswith("infra/pipeline/knowledge_drafts/") or rel.startswith("tests/benchmarks/pipeline/"):
        return "governance_doc"
    if rel.startswith("governance/") or "/prompts/" in rel:
        return "governance_doc"
    if rel.endswith((".md", ".txt", ".rst")):
        return "documentation"
    if rel.startswith("tests/") or "/test_" in rel:
        return "test_file"
    if rel.endswith((".yaml", ".yml", ".toml", ".json")):
        return "config_file"
    if rel.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".kt", ".rs", ".sh", ".ps1")):
        return "implementation_code"
    return "other"


def _effective_file_role(candidate: Mapping[str, Any], path: str, families: List[str], task_profile: Mapping[str, Any]) -> str:
    explicit = str(candidate.get("file_role") or "").strip()
    if explicit:
        return explicit
    return classify_file_role(path, "")


def _effective_candidate_role(candidate: Mapping[str, Any], path: str, families: List[str], task_profile: Mapping[str, Any]) -> str:
    explicit = str(candidate.get("candidate_role") or "").strip()
    if explicit:
        return explicit
    file_role = _effective_file_role(candidate, path, families, task_profile)
    mapped = compatible_candidate_role(file_role)
    if mapped and mapped != "other":
        return mapped
    return _classify_candidate_role(path, families, task_profile)


def _task_archetype(task_profile: Mapping[str, Any]) -> str:
    return normalize_task_archetype(str(task_profile.get("task_archetype") or "implementation_change"))


def _task_mode_role_adjustment(task_profile: Mapping[str, Any], *, file_role: str, purpose: str) -> float:
    weight = file_role_weight(_task_archetype(task_profile), str(file_role or "unknown"), purpose)
    return round((weight - 0.75) * (2.4 if purpose == "target" else (2.0 if purpose == "reference" else 1.6)), 4)


def _target_role_bonus(path: str, role: str, families: List[str], task_profile: Mapping[str, Any]) -> float:
    rel = str(path or "").lower()
    basename = Path(rel).name.lower()
    bonus = 0.0
    if role == "implementation_code":
        bonus += 2.5
    elif role == "config_file":
        bonus += 1.5
    elif role == "test_file":
        bonus += 0.3
    elif role in {"documentation", "governance_doc", "temporary_artifact"}:
        bonus -= 3.5
    family_set = _family_alias_set(families)
    if _is_non_implementation_governance_surface(rel):
        bonus -= 9.0
    if role == "implementation_code" and rel.startswith("infra/pipeline/") and "tool_spec_implementation" not in family_set and "deterministic_patch_helper" not in family_set and not _is_retrieval_ranking_family(families, task_profile):
        bonus -= 4.5
    if not _is_scheduler_family(families):
        if basename == "scheduled_jobs.py":
            bonus -= 10.0
        elif basename == "test_scheduled_jobs.py" or _is_scheduler_surface_path(rel):
            bonus -= 7.2
    if _is_scheduler_family(families):
        if basename == "scheduled_jobs.py":
            bonus += 9.2
        elif basename == "scheduler_retry_manager.py":
            bonus += 6.0
        elif rel.startswith("infra/bin/scheduler_"):
            bonus += 2.8
        elif _is_scheduler_surface_path(rel):
            bonus += 4.0
        elif role == "implementation_code" and rel.startswith("infra/pipeline/"):
            bonus -= 7.5 if "tool_spec_implementation" not in family_set else 0.75
        elif role == "implementation_code":
            bonus -= 2.4
        if role == "test_file" and ("scheduler" in rel or "scheduled_jobs" in rel or "watchdog" in rel):
            bonus += 2.4
        if role == "config_file" and "scheduler" not in rel:
            bonus -= 0.75
    if _is_lab_assistant_memory_family(families, task_profile):
        if role == "implementation_code" and any(marker in rel for marker in MEMORY_OWNER_PATH_MARKERS):
            bonus += 4.2
        if role == "implementation_code" and any(token in rel for token in ["turns", "transcript"]):
            bonus += 1.8
        if rel.endswith(tuple(MEMORY_HUB_BASENAMES)):
            bonus -= 3.4
        if "scheduled_jobs" in rel:
            bonus -= 2.2
    if _is_truth_contract_family(families, task_profile):
        if role == "implementation_code" and rel.startswith("infra/pipeline/"):
            bonus -= 6.0
    if _is_orchestration_checkpoint_family(families, task_profile):
        if role == "implementation_code" and basename == "server_agents_orchestrator.py":
            bonus += 6.2
        elif role == "implementation_code" and any(token in rel for token in ["orchestrator", "workflow", "checkpoint", "turn", "builder", "verifier"]):
            bonus += 4.8
        if role == "test_file" and any(token in rel for token in ["orchestrator", "workflow", "checkpoint", "turn", "builder", "verifier"]):
            bonus += 2.6
        if role == "implementation_code" and rel.startswith("infra/bin/scheduler_"):
            bonus -= 4.8
        if role == "implementation_code" and (basename in {"server_routes_and_main.py", "server_agents.py"} or rel.endswith("static/agents.js")):
            bonus -= 4.8
    if _is_ui_truth_projection_family(families, task_profile):
        if role == "implementation_code" and "operator_snapshot" in rel:
            bonus += 6.0
        elif role == "implementation_code" and not rel.startswith("infra/webui/static/") and any(token in rel for token in ["snapshot", "projection", "status", "health", "reachability", "degraded", "unreachable", "reconnecting"]):
            bonus += 4.4
        elif role == "implementation_code" and rel.startswith("infra/webui/static/") and any(token in rel for token in ["projection", "snapshot", "status", "health", "degraded", "unreachable", "reconnecting"]):
            bonus += 1.6
        if role == "implementation_code" and rel.endswith(UI_STATIC_EXTENSIONS) and any(token in rel for token in ["projection", "snapshot", "status", "health", "degraded", "unreachable", "reconnecting"]):
            bonus += 1.8
        if role == "test_file" and any(token in rel for token in ["operator_snapshot", "snapshot", "projection", "status", "health"]):
            bonus += 2.2
        if role == "implementation_code" and any(token in rel for token in ["delegation", "binding", "command", "transport", "scheduler", "rejoin", "replace", "task_register"]):
            bonus -= 4.8
        if role == "implementation_code" and (basename in {"server_agents.py", "scheduled_jobs.py"} or rel.endswith("static/agents.js")):
            bonus -= 5.1
    if _is_retrieval_ranking_family(families, task_profile):
        if role == "implementation_code" and rel.endswith("infra/pipeline/planning.py"):
            bonus += 2.4
        elif role == "implementation_code" and rel.endswith("infra/pipeline/stages/search_plan.py"):
            bonus += 0.8
        elif role == "implementation_code" and rel.endswith("infra/pipeline/stages/context_ranker.py"):
            bonus += 0.9
        elif role == "implementation_code" and rel.endswith("infra/pipeline/stages/repo_retriever.py"):
            bonus += 0.7
        elif role == "implementation_code" and rel.endswith("infra/pipeline/semantic.py"):
            bonus += 0.45
        elif role == "implementation_code" and rel.startswith("infra/pipeline/"):
            bonus += 0.35
        elif role == "implementation_code" and rel.startswith("infra/bin/pipeline"):
            bonus += 0.4
        elif role == "implementation_code" and (rel.startswith("infra/webui/") or rel.startswith("infra/controlplane/")):
            bonus -= 6.6
        if role == "test_file" and rel.startswith("tests/pipeline/"):
            bonus += 3.4
        if role == "config_file" and rel.startswith("infra/pipeline/knowledge/"):
            bonus += 2.2
        if role == "implementation_code" and any(token in rel for token in RETRIEVAL_RANKING_HUB_TOKENS):
            bonus -= 5.6
    if "deterministic_patch_helper" in families:
        if any(tok in rel for tok in ["tools/patch_helpers", "tools/pipeline", "tests/"]):
            bonus += 3.0
    if "tool_spec_implementation" in family_set:
        if _is_tool_spec_surface_path(rel):
            bonus += 5.0
        elif role == "implementation_code" and rel.startswith("infra/pipeline/"):
            bonus += 1.0
        elif role == "implementation_code":
            bonus -= 1.0
    if "mobile_android_ui" in families:
        if any(tok in rel for tok in ["android", "mobile", "app/src", "infra/webui/static/"]):
            bonus += 2.5
    elif _is_mobile_ui_path(rel):
        bonus -= 4.2
        if _is_orchestration_checkpoint_family(families, task_profile) or _is_scheduler_family(families) or _is_retrieval_ranking_family(families, task_profile):
            bonus -= 2.0
        if _is_ui_truth_projection_family(families, task_profile) and not (
            rel.startswith("infra/webui/static/")
            or any(token in rel for token in ["projection", "snapshot", "status", "health", "degraded", "unreachable", "reconnecting"])
        ):
            bonus -= 1.8
        if _is_truth_contract_family(families, task_profile) or _is_lab_assistant_memory_family(families, task_profile) or _is_retrieval_ranking_family(families, task_profile):
            bonus -= 2.0
    if "testing_verification" in families and role == "test_file":
        bonus += 2.0
    if any(f in families for f in ["documentation_contracts"]) and role in {"documentation", "governance_doc"}:
        bonus += 2.5
    return bonus


def _role_is_editable(role: str, families: List[str], task_profile: Mapping[str, Any]) -> bool:
    family_set = _family_alias_set(families)
    if role == "implementation_code":
        return True
    if role == "config_file":
        return True
    if role == "test_file":
        return _supports_paired_test_editing(families, task_profile)
    if role in {"documentation", "governance_doc"}:
        return _is_docs_task(families, task_profile)
    return False

def _job_terms(job_contract: Mapping[str, Any], task_profile: Mapping[str, Any]) -> set[str]:
    parts = [
        str(job_contract.get("title") or ""),
        str(job_contract.get("description") or ""),
        str(job_contract.get("objective") or ""),
        str(job_contract.get("issue_context", {}).get("stack_trace") or ""),
        " ".join([str(x) for x in job_contract.get("issue_context", {}).get("failing_tests", [])]),
        " ".join([str(x) for x in job_contract.get("issue_context", {}).get("changed_files_hint", [])]),
        " ".join([str(x) for x in job_contract.get("acceptance_criteria", [])]),
        " ".join([str(x) for x in task_profile.get("secondary_tags", [])]),
        str(job_contract.get("issue_context", {}).get("user_notes") or ""),
    ]
    return _word_set(" ".join(parts))


def _task_cross_flags(task_profile: Mapping[str, Any], intake_assessment: Mapping[str, Any]) -> Set[str]:
    flags: Set[str] = set()
    for value in list(task_profile.get("cross_cutting_flags") or []):
        text = str(value or "").strip().lower()
        if text:
            flags.add(text)
    for value in list(intake_assessment.get("cross_cutting_flags") or []):
        text = str(value or "").strip().lower()
        if text:
            flags.add(text)
    return flags


def _failure_mode_texts(intake_assessment: Mapping[str, Any]) -> List[str]:
    failure_taxonomy = dict(intake_assessment.get("failure_taxonomy") or {})
    texts: List[str] = []
    for value in failure_taxonomy.values():
        if isinstance(value, list):
            texts.extend(str(item or "").strip().lower() for item in value if str(item or "").strip())
    return texts


def _is_omnibus_test_candidate(entry: Mapping[str, Any]) -> bool:
    path = str(entry.get("path") or "").lower()
    return bool(
        str(entry.get("candidate_role") or "") == "test_file"
        and any(token in path for token in ["integration", "e2e", "end_to_end", "workflow", "scenario", "omnibus"])
    )


def _stage5_cap_rescue(entry: Mapping[str, Any], *, lane_kind: str) -> bool:
    ownership_role = str(entry.get("ownership_role") or "")
    actionability = dict(entry.get("actionability_decision") or {})
    if lane_kind == "target":
        return bool(
            ownership_role == "core_owner"
            or actionability.get("eligible_for_target")
            or str(entry.get("candidate_role_guess") or "") == "owner-like"
        )
    return bool(
        ownership_role in {"core_owner", "local_contract_doc", "test_surface"}
        or str(entry.get("candidate_role_guess") or "") in {"owner-like", "contract/reference-like", "test-witness-like"}
    )


def _mark_stage5_cap_suppression(ranked_lookup: Mapping[str, MutableMapping[str, Any]], path: str, reason: str) -> None:
    ranked_entry = ranked_lookup.get(path)
    if not ranked_entry:
        return
    ranked_entry["selection_cap_suppressed"] = True
    existing = [str(item) for item in list(ranked_entry.get("selection_cap_reasons") or []) if str(item).strip()]
    existing.append(reason)
    ranked_entry["selection_cap_reasons"] = _dedupe_strings(existing)
    why_not = [str(item) for item in list(ranked_entry.get("why_not_other_roles") or []) if str(item).strip()]
    why_not.append(f"suppressed by deterministic cap: {reason}")
    ranked_entry["why_not_other_roles"] = _dedupe_strings(why_not)


def _apply_stage5_lane_caps(
    selected_entries: Sequence[Mapping[str, Any]],
    *,
    replacement_pool: Sequence[Mapping[str, Any]],
    max_keep: int,
    lane_kind: str,
    policy_config: Mapping[str, Any],
    ranked_lookup: Mapping[str, MutableMapping[str, Any]],
) -> List[Dict[str, Any]]:
    desired = min(max_keep, len(list(selected_entries)))
    duplicate_cap = max(1, int(policy_config.get("duplicate_family_cap") or 1))
    basename_cap = max(1, int(policy_config.get("basename_cap") or 1))
    omnibus_cap = max(1, int(policy_config.get("omnibus_test_cap") or 1))
    hub_caps = dict(policy_config.get("hub_family_caps") or {})
    passive_caps = dict(policy_config.get("passive_surface_caps") or {})
    passive_cap = int(passive_caps.get(lane_kind) or passive_caps.get("default") or 1)

    kept: List[Dict[str, Any]] = []
    kept_paths: Set[str] = set()
    duplicate_counts: Dict[str, int] = {}
    basename_counts: Dict[str, int] = {}
    hub_counts: Dict[str, int] = {}
    passive_count = 0
    omnibus_count = 0

    def _fits(entry: Mapping[str, Any]) -> Tuple[bool, str]:
        rescue = _stage5_cap_rescue(entry, lane_kind=lane_kind)
        path = str(entry.get("path") or "")
        family_key = str(entry.get("duplicate_family_key") or path)
        basename = Path(path).name.lower()
        hub_family = str(entry.get("hub_family") or "")
        if path in kept_paths:
            return False, "duplicate-path"
        if bool(entry.get("passive_surface_flag")) and passive_count >= passive_cap and not rescue:
            return False, "passive-surface-cap"
        if duplicate_counts.get(family_key, 0) >= duplicate_cap and not rescue:
            return False, f"duplicate-family-cap:{family_key}"
        if basename_counts.get(basename, 0) >= basename_cap and not rescue:
            return False, f"basename-cap:{basename}"
        if _is_omnibus_test_candidate(entry) and omnibus_count >= omnibus_cap and not rescue:
            return False, "omnibus-test-cap"
        if hub_family:
            hub_cap = max(1, int(hub_caps.get(hub_family) or 1))
            if hub_counts.get(hub_family, 0) >= hub_cap and not rescue:
                return False, f"hub-family-cap:{hub_family}"
        return True, ""

    def _accept(entry: Mapping[str, Any]) -> None:
        nonlocal passive_count, omnibus_count
        item = dict(entry)
        path = str(item.get("path") or "")
        family_key = str(item.get("duplicate_family_key") or path)
        basename = Path(path).name.lower()
        hub_family = str(item.get("hub_family") or "")
        kept.append(item)
        kept_paths.add(path)
        duplicate_counts[family_key] = duplicate_counts.get(family_key, 0) + 1
        basename_counts[basename] = basename_counts.get(basename, 0) + 1
        if hub_family:
            hub_counts[hub_family] = hub_counts.get(hub_family, 0) + 1
        if bool(item.get("passive_surface_flag")):
            passive_count += 1
        if _is_omnibus_test_candidate(item):
            omnibus_count += 1

    for entry in selected_entries:
        ok, reason = _fits(entry)
        if ok:
            _accept(entry)
        else:
            _mark_stage5_cap_suppression(ranked_lookup, str(entry.get("path") or ""), reason)

    for entry in replacement_pool:
        if len(kept) >= desired:
            break
        if str(entry.get("path") or "") in kept_paths:
            continue
        ok, reason = _fits(entry)
        if ok:
            _accept(entry)
        else:
            _mark_stage5_cap_suppression(ranked_lookup, str(entry.get("path") or ""), reason)

    return kept[:max_keep]


def _rank_context_pass(
    *,
    file_candidates: Sequence[Mapping[str, Any]],
    job_contract: Mapping[str, Any],
    intake_assessment: Mapping[str, Any],
    task_profile: Mapping[str, Any],
    search_plan: Mapping[str, Any],
    planning_assets: Mapping[str, Any],
    subsystem_asset: Mapping[str, Any],
    families: List[str],
    replace_spec: Optional[Mapping[str, Any]],
    is_global_replace: bool,
    query_terms: set[str],
    changed_hints: set[str],
    explicit_file_hints: set[str],
    failing_tests: str,
    min_file_score: float,
    semantic_bonus_enabled: bool,
    semantic_mode: str,
    semantic_weight: float,
    max_selected_files: int,
    max_snippets: int,
    score_margin_keep_secondary: float,
    narrow_task: bool,
    max_targets_per_subsystem: int,
    policy_config: Mapping[str, Any],
) -> Dict[str, Any]:
    ranked: List[Dict[str, Any]] = []
    snippet_total = 0
    seen_paths: Set[str] = set()
    family_set = _family_alias_set(families)
    docs_task = _is_docs_task(families, task_profile)
    docs_reference_preferred = _prefers_docs_reference(families, task_profile)
    test_first_task = _is_test_first_task(families, task_profile)
    cross_flags = _task_cross_flags(task_profile, intake_assessment)
    failure_text_blob = " ".join(_failure_mode_texts(intake_assessment))
    job_text_blob = " ".join(sorted(query_terms))
    recovery_focus = _is_scheduler_family(families) or bool(cross_flags.intersection(RECOVERY_FOCUS_FLAGS)) or any(term in failure_text_blob for term in RECOVERY_FOCUS_TERMS)
    retrieval_ranking_focus = _is_retrieval_ranking_family(families, task_profile)
    memory_focus = (not retrieval_ranking_focus) and (_is_lab_assistant_memory_family(families, task_profile) or any(term in (failure_text_blob + " " + job_text_blob) for term in MEMORY_FOCUS_TERMS))
    truth_contract_focus = _is_truth_contract_family(families, task_profile)
    orchestration_focus = _is_orchestration_checkpoint_family(families, task_profile)
    ui_projection_focus = _is_ui_truth_projection_family(families, task_profile)
    orchestration_focus = _is_orchestration_checkpoint_family(families, task_profile)
    ui_projection_focus = _is_ui_truth_projection_family(families, task_profile)
    retrieval_ranking_focus = _is_retrieval_ranking_family(families, task_profile)
    mailbox_writeback_seam_focus = detect_mailbox_writeback_seam_focus(
        job_contract,
        task_profile=task_profile,
        search_plan=search_plan,
        query_terms=query_terms,
    )
    operator_focus = bool(cross_flags.intersection(OPERATOR_FOCUS_FLAGS)) or any(term in failure_text_blob for term in ("manual", "operator", "human failure", "human"))
    validation_focus = operator_focus or bool(cross_flags.intersection(VALIDATION_FOCUS_FLAGS)) or ui_projection_focus or retrieval_ranking_focus
    query_subsystem_index = _query_coverage_subsystem_index(search_plan)
    filename_alias_index = _subsystem_filename_alias_index(subsystem_asset)
    active_subsystem_hypotheses = _dedupe_strings(search_plan.get("subsystem_hypotheses") or [])
    active_failure_categories = _active_failure_categories(intake_assessment)
    active_invariant_buckets = _active_invariant_buckets(intake_assessment, task_profile, search_plan, families)
    active_query_coverage_tags = _query_cover_tags(search_plan)

    for candidate in file_candidates:
        path = str(candidate.get("path") or "").strip()
        if not path or path in seen_paths:
            continue
        seen_paths.add(path)
        path_lower = path.lower()
        matched_terms = {str(x).lower() for x in candidate.get("matched_terms", [])}
        matched_symbols = {str(x) for x in candidate.get("matched_symbols", [])}
        line_hits = list(candidate.get("line_hits") or [])
        lexical_overlap = len(query_terms.intersection(matched_terms.union(_word_set(path))))
        symbol_bonus = min(6, len(matched_symbols))
        test_bonus = 1 if any(term in failing_tests for term in _word_set(path)) else 0
        basename = Path(path).name.lower()
        basename_normalized = basename.replace("_", "").replace("-", "")
        symbol_in_path_bonus = 5.0 if any(sym.lower().replace("_", "").replace("-", "") in basename_normalized for sym in matched_symbols) else 0.0
        explicit_file_hint = basename in explicit_file_hints or path_lower in explicit_file_hints
        path_hint_bonus = 2 if basename in changed_hints or explicit_file_hint else 0
        line_hit_bonus = min(5, len(line_hits))
        giant_penalty = 0 if is_global_replace else (2 if path_lower.endswith((".min.js", ".lock")) else 0)
        vendor_penalty = 2 if any(part in path_lower.split("/") for part in ["vendor", "node_modules", "dist", "build"]) else 0
        file_role = _effective_file_role(candidate, path_lower, families, task_profile)
        candidate_role = _effective_candidate_role(candidate, path_lower, families, task_profile)
        candidate_intent_lane = str(candidate.get("candidate_intent_lane") or ("edit_likely" if candidate_role in {"implementation_code", "config_file"} else "context_only"))
        context_intent_type = str(candidate.get("context_intent_type") or "")
        structural_family_tags = [str(item) for item in list(candidate.get("structural_family_tags") or []) if str(item).strip()]
        target_bonus = _target_role_bonus(path_lower, candidate_role, families, task_profile) + _task_mode_role_adjustment(task_profile, file_role=file_role, purpose="target")
        reference_bonus = _reference_role_bonus(path_lower, candidate_role, families, task_profile) + _task_mode_role_adjustment(task_profile, file_role=file_role, purpose="reference")
        hub_kind = _core_hub_kind(path_lower)
        reference_only_guard = _forbidden_modify_path(path_lower, families=families, task_profile=task_profile, is_global_replace=is_global_replace)
        implementation_target_blocked = _is_forbidden_implementation_target(path_lower)
        doc_penalty = 0.0
        if _looks_non_implementation_doc(path_lower) and not docs_reference_preferred:
            doc_penalty += 4.0
        if candidate_role == "temporary_artifact":
            doc_penalty += 2.5
        if _is_contamination_surface(path_lower) and not docs_reference_preferred:
            doc_penalty += 4.8
        if "system_hardening" in list(task_profile.get("secondary_tags") or []) and candidate_role in {"documentation", "governance_doc"}:
            doc_penalty += 1.5
        exact_occurrence_bonus = 0
        if is_global_replace and replace_spec:
            old_term = str(replace_spec.get("old") or "").lower()
            exact_occurrence_bonus = sum(1 for hit in line_hits if old_term in str(hit.get("preview") or "").lower())
        semantic_score = float(candidate.get("scores", {}).get("semantic_score") or 0.0)
        graph_score = float(candidate.get("scores", {}).get("graph_score") or 0.0)
        candidate_subsystems = _infer_candidate_subsystems(
            candidate,
            search_plan=search_plan,
            subsystem_asset=subsystem_asset,
            query_subsystem_index=query_subsystem_index,
            filename_alias_index=filename_alias_index,
        )
        semantic_target_bonus = semantic_weight * semantic_score * _semantic_target_weight(candidate_role, families=families, task_profile=task_profile) if semantic_bonus_enabled and semantic_mode in {"retrieve_and_rank", "rank_only"} else 0.0
        semantic_reference_bonus = semantic_weight * semantic_score * _semantic_reference_weight(candidate_role, families=families, task_profile=task_profile) if semantic_bonus_enabled and semantic_mode in {"retrieve_and_rank", "rank_only"} else 0.0
        covers = {str(item or "").strip().lower() for item in list(candidate.get("covers") or []) if str(item or "").strip()}
        coverage_score = float(candidate.get("coverage_score") or 0.0)
        superficial_penalty = float(candidate.get("superficial_match_penalty") or 0.0)
        explicit_path_request = bool(path_hint_bonus or _path_keyword_overlap(path_lower, query_terms) or bool(matched_symbols))
        owner_cover_count = len(covers.intersection(OWNER_SIGNAL_COVERS))
        hub_cover_count = len(covers.intersection(HUB_SPAN_COVERS))
        owner_bonus = min(1.6, 0.38 * owner_cover_count)
        if candidate_role == "implementation_code" and "dispatch_path" in covers and not hub_kind:
            owner_bonus += 0.35
        if candidate_role == "config_file" and "validation_path" in covers:
            owner_bonus += 0.2
        hub_target_penalty = 0.0
        hub_reference_penalty = 0.0
        if hub_kind:
            hub_target_penalty += {
                "scheduler_hub": 4.8,
                "webui_router_hub": 4.4,
                "webui_orchestrator_hub": 3.6,
                "pipeline_planner_hub": 4.6,
                "pipeline_stage_hub": 4.2,
                "benchmark_harness": 3.2,
                "broad_test_harness": 2.6,
            }.get(hub_kind, 2.8)
            hub_reference_penalty += {
                "scheduler_hub": 1.1,
                "webui_router_hub": 0.95,
                "webui_orchestrator_hub": 0.8,
                "pipeline_planner_hub": 1.0,
                "pipeline_stage_hub": 0.9,
                "benchmark_harness": 0.65,
                "broad_test_harness": 0.55,
            }.get(hub_kind, 0.6)
            if hub_cover_count >= 4:
                hub_target_penalty += 1.5
                hub_reference_penalty += 0.35
            if not explicit_path_request:
                hub_target_penalty += 0.8
                hub_reference_penalty += 0.15
            if owner_cover_count == 0:
                hub_target_penalty += 0.6
                hub_reference_penalty += 0.1
            if explicit_path_request and hub_kind != "broad_test_harness":
                hub_target_penalty *= 0.25
                hub_reference_penalty *= 0.25
            elif narrow_task:
                hub_target_penalty *= 1.3
                hub_reference_penalty *= 1.15
        if _is_scheduler_family(families) and path_lower.endswith("scheduled_jobs.py") and query_terms.intersection(SCHEDULER_NARROW_OWNER_MARKERS):
            hub_target_penalty += 2.4
            hub_reference_penalty += 0.45
        coverage_target_bonus = min(1.1, coverage_score * (0.28 if candidate_role == "implementation_code" else 0.18))
        coverage_reference_bonus = min(0.65, coverage_score * 0.16)
        if recovery_focus:
            if "recovery_responsibility" in covers:
                coverage_target_bonus += 3.1
                coverage_reference_bonus += 0.95
            elif "recovery_path" in covers:
                coverage_target_bonus += 1.25
                coverage_reference_bonus += 0.45
            if "state_mutation" in covers and covers.intersection({"recovery_path", "recovery_responsibility"}):
                coverage_target_bonus += 0.55
            if "ownership_control" in covers and "recovery_responsibility" in covers:
                coverage_target_bonus += 0.45
        if operator_focus and "operator_flow" in covers:
            coverage_target_bonus += 0.35
            coverage_reference_bonus += 0.2
        if validation_focus and "validation_path" in covers:
            coverage_target_bonus += 0.28
            coverage_reference_bonus += 0.18
        if _is_scheduler_family(families) and "dispatch_path" in covers:
            coverage_target_bonus += 0.95
            if "recovery_responsibility" not in covers:
                coverage_target_bonus += 0.25
        if memory_focus:
            if "truth_boundary" in covers:
                coverage_target_bonus += 1.15
                coverage_reference_bonus += 0.35
            if "reconciliation_path" in covers:
                coverage_target_bonus += 1.0
                coverage_reference_bonus += 0.3
            if "task_register_flow" in covers:
                coverage_target_bonus += 1.35
                coverage_reference_bonus += 0.34
                if "task register" in matched_terms or "task_register" in path_lower:
                    coverage_target_bonus += 0.45
                    coverage_reference_bonus += 0.12
            if "transcript_flow" in covers:
                coverage_target_bonus += 0.82
                coverage_reference_bonus += 0.24
            if "ledger_evidence" in covers:
                coverage_target_bonus += 0.7
                coverage_reference_bonus += 0.45
            if "memory_retrieval" in covers:
                coverage_target_bonus += 0.62
                coverage_reference_bonus += 0.3
            if "writeback_boundary" in covers:
                coverage_target_bonus += 0.72
                coverage_reference_bonus += 0.25
            if "memory_ownership" in covers:
                coverage_target_bonus += 0.58
                coverage_reference_bonus += 0.2
        if retrieval_ranking_focus:
            if "retrieval_owner" in covers:
                coverage_target_bonus += 1.35
                coverage_reference_bonus += 0.22
            if "retrieval_scoring" in covers:
                coverage_target_bonus += 0.25
                coverage_reference_bonus += 0.72
            if "retrieval_search" in covers:
                coverage_target_bonus += 0.2
                coverage_reference_bonus += 0.74
            if "retrieval_answer" in covers:
                coverage_target_bonus += 0.22
                coverage_reference_bonus += 0.76
            if "retrieval_rules" in covers:
                coverage_target_bonus -= 0.12
                coverage_reference_bonus += 0.64
        if not _is_scheduler_family(families) and (_is_scheduler_surface_path(path_lower) or Path(path_lower).name.lower() == "test_scheduled_jobs.py"):
            if not _query_mentions_scheduler_surface(query_terms) or truth_contract_focus or memory_focus or retrieval_ranking_focus or orchestration_focus:
                implementation_target_blocked = True
        if _is_non_implementation_governance_surface(path_lower) and not docs_task:
            implementation_target_blocked = True
        if (
            _is_mobile_ui_path(path_lower)
            and "mobile_android_ui" not in family_set
            and not ui_projection_focus
            and not _query_mentions_mobile_surface(query_terms)
            and (truth_contract_focus or memory_focus or retrieval_ranking_focus)
        ):
            implementation_target_blocked = True
        centrality_score, centrality_reasons = _target_centrality_score(
            candidate,
            families=families,
            subsystem_hypotheses=active_subsystem_hypotheses,
            candidate_subsystems=candidate_subsystems,
            filename_alias_index=filename_alias_index,
            covers=covers,
            candidate_role=candidate_role,
            graph_score=graph_score,
            lexical_overlap=lexical_overlap,
            matched_symbols=matched_symbols,
            explicit_path_request=bool(explicit_path_request),
            hub_kind=str(hub_kind or ""),
            owner_cover_count=owner_cover_count,
            superficial_penalty=superficial_penalty,
            recovery_focus=recovery_focus,
            operator_focus=operator_focus,
            validation_focus=validation_focus,
        )
        ownership_role = _ownership_role_for_candidate(
            path=path,
            candidate_role=candidate_role,
            families=families,
            task_profile=task_profile,
            mailbox_writeback_seam_focus=mailbox_writeback_seam_focus,
            candidate_subsystems=candidate_subsystems,
            covers=covers,
            owner_cover_count=owner_cover_count,            graph_score=graph_score,
        )
        coverage_detail = _score_candidate_coverage_v2(
            candidate,
            intake_assessment=intake_assessment,
            task_profile=task_profile,
            search_plan=search_plan,
            candidate_subsystems=candidate_subsystems,
            ownership_role=ownership_role,
            candidate_role=candidate_role,
            families=families,
            assets=planning_assets,
        ) if (active_failure_categories or active_invariant_buckets or cross_flags or active_query_coverage_tags) else {
            "score": 0.0,
            "matched_failure_categories": [],
            "matched_invariant_buckets": [],
            "matched_cross_cutting_flags": [],
            "matched_subsystems": [],
            "matched_query_coverage": [],
            "coverage_breadth": 0,
        }
        matched_subsystems = _dedupe_strings(coverage_detail.get("matched_subsystems") or [])
        primary_subsystems = active_subsystem_hypotheses[:2]
        subsystem_fit_score = 0.0
        if matched_subsystems:
            subsystem_fit_score += 0.7 + 0.2 * min(2, len(matched_subsystems))
            if candidate_subsystems and str(candidate_subsystems[0] or "").strip() in primary_subsystems:
                subsystem_fit_score += 0.45
        elif candidate_subsystems and not active_subsystem_hypotheses:
            subsystem_fit_score += 0.15
        subsystem_fit_score = round(min(1.6, subsystem_fit_score), 4)
        subsystem_target_guard = bool(
            active_subsystem_hypotheses
            and not matched_subsystems
            and not explicit_path_request
            and ownership_role != "core_owner"
            and owner_cover_count == 0
        )
        passive_relevance = _score_passive_relevance_penalty(
            candidate,
            passive_asset=_coerce_mapping(planning_assets.get("passive_relevance_patterns")),
            families=families,
            task_profile=task_profile,
            ownership_role=ownership_role,
            candidate_role=candidate_role,
            file_role=file_role,
            covers=covers,
            path_lower=path_lower,
        )
        superficial_detail = _score_superficial_match_penalty_v2(
            candidate,
            families=families,
            task_profile=task_profile,
            candidate_role=candidate_role,
            file_role=file_role,
            path_lower=path_lower,
            lexical_overlap=lexical_overlap,
            matched_symbols=matched_symbols,
            covers=covers,
            hub_kind=str(hub_kind or ""),
            explicit_path_request=bool(explicit_path_request),
            owner_cover_count=owner_cover_count,
            graph_score=graph_score,
            ownership_role=ownership_role,
        )
        family_owner_fit = _family_owner_fit(
            path=path,
            families=families,
            task_profile=task_profile,
            mailbox_writeback_seam_focus=mailbox_writeback_seam_focus,
            ownership_role=ownership_role,
            covers=covers,
            candidate_subsystems=candidate_subsystems,        )
        if family_owner_fit:
            target_bonus += family_owner_fit
            reference_bonus += max(-0.25, min(0.55, family_owner_fit * 0.18))
        if ownership_role == "core_owner":
            target_bonus += 2.15 if truth_contract_focus else 1.85
            reference_bonus += 0.35
            if hub_kind:
                hub_target_penalty *= 0.18
                hub_reference_penalty *= 0.35
        elif ownership_role == "boundary_sibling":
            target_bonus -= 0.95
            reference_bonus += 0.75
        elif ownership_role == "projection":
            target_bonus -= 1.55
            reference_bonus += 0.65
        elif ownership_role == "read_only_route":
            target_bonus -= 1.35
            reference_bonus += 0.7
        elif ownership_role == "test_surface":
            target_bonus += 0.75
            reference_bonus += 0.35
        elif ownership_role == "local_contract_doc":
            target_bonus -= 1.3
            reference_bonus += 1.6
        elif ownership_role == "hub":
            target_bonus -= 2.2
            reference_bonus += 0.15

        v2_coverage_score = float(coverage_detail.get("score") or 0.0)
        coverage_target_bonus += min(
            1.2,
            v2_coverage_score * (0.46 if ownership_role == "core_owner" else (0.4 if candidate_role in {"implementation_code", "config_file"} else 0.28)),
        )
        coverage_reference_bonus += min(
            0.6,
            v2_coverage_score * (0.24 if ownership_role not in {"hub", "projection", "read_only_route"} else 0.14),
        )
        if candidate_role == "test_file" and (coverage_detail.get("matched_failure_categories") or coverage_detail.get("matched_cross_cutting_flags")):
            coverage_reference_bonus += min(0.22, v2_coverage_score * 0.1)

        passive_penalty_score = float(passive_relevance.get("score") or 0.0)
        passive_target_penalty = passive_penalty_score * (1.0 if ownership_role not in {"core_owner", "test_surface"} else 0.18)
        passive_reference_penalty = passive_penalty_score * (0.72 if ownership_role not in {"core_owner", "test_surface"} else 0.14)

        v2_superficial_penalty = float(superficial_detail.get("score") or 0.0)
        if superficial_penalty and v2_superficial_penalty:
            superficial_penalty = min(3.8, max(superficial_penalty, v2_superficial_penalty) + 0.2)
        else:
            superficial_penalty = max(superficial_penalty, v2_superficial_penalty)
        superficial_target_penalty = superficial_penalty * (1.15 if candidate_role == "implementation_code" else 0.8)
        superficial_reference_penalty = superficial_penalty * 0.6

        meaningful_owner_evidence = bool(
            candidate_role in {"implementation_code", "config_file"}
            and (owner_cover_count > 0 or v2_coverage_score >= 1.0 or family_owner_fit >= 1.0)
        )
        if meaningful_owner_evidence:
            combined_target_penalty = passive_target_penalty + superficial_target_penalty
            max_target_penalty = 2.15 if ownership_role == "core_owner" else 1.75
            if combined_target_penalty > max_target_penalty and combined_target_penalty > 0:
                penalty_scale = max_target_penalty / combined_target_penalty
                passive_target_penalty *= penalty_scale
                superficial_target_penalty *= penalty_scale
            passive_reference_penalty *= 0.85
            superficial_reference_penalty *= 0.9

        targetability_score = round(
            _base_target_score(candidate, candidate_role)
            + lexical_overlap * 1.15
            + symbol_bonus * 1.2
            + symbol_in_path_bonus
            + test_bonus
            + path_hint_bonus
            + line_hit_bonus * 0.9
            + exact_occurrence_bonus
            + target_bonus
            + coverage_target_bonus
            + semantic_target_bonus
            + owner_bonus
            + centrality_score
            + subsystem_fit_score * 0.55
            - giant_penalty
            - vendor_penalty
            - doc_penalty
            - superficial_target_penalty
            - passive_target_penalty
            - hub_target_penalty,
            4,
        )
        reference_value_score = round(
            _base_reference_score(candidate, candidate_role)
            + lexical_overlap * 0.75
            + min(2, line_hit_bonus) * 0.9
            + min(2, symbol_bonus) * 0.45
            + reference_bonus
            + coverage_reference_bonus
            + semantic_reference_bonus
            + max(0.0, min(0.55, centrality_score * 0.25))
            + subsystem_fit_score * 0.18
            - giant_penalty * 0.5
            - vendor_penalty
            - (1.25 if candidate_role == "temporary_artifact" else 0.0)
            - superficial_reference_penalty
            - passive_reference_penalty
            - hub_reference_penalty,
            4,
        )

        if candidate_intent_lane == "context_only":
            targetability_score = round(targetability_score - (1.75 if context_intent_type not in {"fallback_hub", "validation_context"} else 0.85), 4)
            reference_value_score = round(reference_value_score + {
                "witness_test": 0.7,
                "contract_reference": 0.65,
                "validation_context": 0.45,
                "fallback_hub": 0.25,
                "broad_docs_reporting": 0.1,
            }.get(context_intent_type, 0.25), 4)
        elif subsystem_fit_score > 0 and candidate_intent_lane == "edit_likely":
            targetability_score = round(targetability_score + min(0.45, subsystem_fit_score * 0.18), 4)

        if targetability_score < min_file_score and reference_value_score < min_file_score:
            continue

        edit_allowed = _role_is_editable(candidate_role, families, task_profile) and not reference_only_guard and not implementation_target_blocked
        if candidate_role == "test_file" and not _supports_paired_test_editing(families, task_profile):
            edit_allowed = False
        selection_lane = "implementation_target" if edit_allowed else "reference_context"
        if selection_lane == "implementation_target" and candidate_intent_lane == "context_only":
            controlled_fallback_seed = bool(
                (
                    candidate_role == "test_file"
                    and ownership_role == "test_surface"
                    and _supports_paired_test_editing(families, task_profile)
                )
                or
                context_intent_type in {"fallback_hub", "validation_context"}
                and (
                    explicit_path_request
                    or bool(coverage_detail.get("matched_cross_cutting_flags"))
                    or bool(covers.intersection({"dispatch_path", "operator_flow", "validation_path"}))
                )
            )
            if not controlled_fallback_seed:
                selection_lane = "reference_context"
                edit_allowed = False
        if selection_lane == "implementation_target" and reference_value_score > targetability_score + 1.0 and candidate_role not in {"implementation_code", "config_file"}:
            selection_lane = "reference_context"
        if selection_lane == "implementation_target" and subsystem_target_guard and (hub_kind or centrality_score < 1.0):
            selection_lane = "reference_context"
        if reference_only_guard or implementation_target_blocked:
            selection_lane = "reference_context"
            edit_allowed = False

        task_archetype = _task_archetype(task_profile)
        if task_archetype in {"repo_assessment", "information_retrieval", "documentation_contract"}:
            selection_lane = "reference_context"
            edit_allowed = False
        force_reference_for_passive = (
            passive_penalty_score >= 0.95
            and ownership_role in {"projection", "read_only_route", "hub", "boundary_sibling"}
            and not _is_ui_truth_projection_family(families, task_profile)
            and owner_cover_count == 0
            and family_owner_fit < 0.95
        )
        if force_reference_for_passive:
            selection_lane = "reference_context"
            edit_allowed = False

        reasons: List[str] = []
        if matched_symbols:
            reasons.append("matched explicit symbols")
        if "recovery_responsibility" in covers:
            reasons.append("owns recovery or repair flow")
        elif recovery_focus and "recovery_path" in covers:
            reasons.append("covers recovery failure surface")
        if operator_focus and "operator_flow" in covers:
            reasons.append("handles operator-facing control flow")
        if validation_focus and "validation_path" in covers:
            reasons.append("contains safety validation path")
        if truth_contract_focus:
            if ownership_role == "core_owner":
                reasons.append("dominant truth-contract owner surface")
            elif ownership_role == "boundary_sibling":
                reasons.append("adjacent truth boundary retained as sibling context")
            elif ownership_role == "projection":
                reasons.append("projection surface demoted behind canonical state owner")
            elif ownership_role == "read_only_route":
                reasons.append("read-only route demoted behind ownership module")
            elif ownership_role == "test_surface":
                reasons.append("paired validation surface for dominant owner")
            elif ownership_role == "local_contract_doc":
                reasons.append("local contract document promoted as reference context")
        elif orchestration_focus:
            if ownership_role == "core_owner":
                reasons.append("dominant orchestration checkpoint owner surface")
            elif ownership_role == "boundary_sibling":
                reasons.append("adjacent workflow or recovery boundary retained as context")
            elif ownership_role == "hub":
                reasons.append("broad orchestration hub demoted behind checkpoint owner")
            elif ownership_role == "test_surface":
                reasons.append("paired crash-window and replay-safety validation surface")
        elif ui_projection_focus:
            if ownership_role == "core_owner":
                reasons.append("dominant ui truth-projection owner surface")
            elif ownership_role == "boundary_sibling":
                reasons.append("command or transport sibling demoted behind projection owner")
            elif ownership_role == "hub":
                reasons.append("generic control surface demoted behind honest projection owner")
            elif ownership_role == "test_surface":
                reasons.append("paired stale or unreachable projection validation surface")
        elif retrieval_ranking_focus:
            if ownership_role == "core_owner":
                reasons.append("dominant retrieval-ranking owner surface")
            elif ownership_role == "boundary_sibling":
                reasons.append("adjacent retrieval boundary retained as context")
            elif ownership_role == "hub":
                reasons.append("non-ranking hub demoted behind evidence assembly owner")
            elif ownership_role == "test_surface":
                reasons.append("paired freshness and evidence-order validation surface")
        elif _is_scheduler_family(families):
            if ownership_role == "core_owner":
                reasons.append("dominant scheduler reliability owner surface")
            elif ownership_role == "hub":
                reasons.append("broad control hub demoted behind scheduler owner")
            elif ownership_role == "test_surface":
                reasons.append("paired scheduler regression surface")
        if memory_focus:
            if "truth_boundary" in covers:
                reasons.append("owns truth-boundary enforcement")
            if "reconciliation_path" in covers:
                reasons.append("handles reconciliation or correction flow")
            if "task_register_flow" in covers:
                reasons.append("owns task-register state")
            if "transcript_flow" in covers:
                reasons.append("handles transcript or turn evidence")
            if "ledger_evidence" in covers:
                reasons.append("anchors provenance or ledger evidence")
            if "writeback_boundary" in covers:
                reasons.append("controls writeback or projection boundary")
        if _is_scheduler_family(families) and "dispatch_path" in covers:
            reasons.append("owns core scheduling dispatch path")
        if family_owner_fit >= 1.4:
            reasons.append("strong family-specific owner fit")
        elif family_owner_fit < 0:
            reasons.append("weak family fit demoted despite central-looking surface")
        if semantic_target_bonus > 0 or semantic_reference_bonus > 0:
            reasons.append("boosted by semantic similarity")
        if path_hint_bonus:
            reasons.append("matches changed_files_hint")
        if test_bonus:
            reasons.append("near failing test evidence")
        if lexical_overlap:
            reasons.append("overlaps objective keywords")
        if owner_bonus > 0.4:
            reasons.append("direct owner signals outrank hub context")
        if coverage_detail.get("matched_failure_categories"):
            reasons.append("covers active failure categories")
        if coverage_detail.get("matched_invariant_buckets"):
            reasons.append("covers active invariants or derived requirements")
        if coverage_detail.get("matched_cross_cutting_flags"):
            reasons.append("covers active cross-cutting concerns")
        if coverage_detail.get("matched_subsystems"):
            reasons.append("covers active subsystem hypotheses")
        elif subsystem_target_guard:
            reasons.append("demoted for weak subsystem alignment")
        if coverage_detail.get("matched_query_coverage"):
            reasons.append("matches search-plan coverage targets")
        if candidate_intent_lane == "context_only":
            reasons.append(f"typed context lane:{context_intent_type or 'context_only'}")
        elif candidate_intent_lane == "edit_likely":
            reasons.append("edit-likely candidate lane")
        reasons.extend(centrality_reasons)
        if hub_target_penalty > 0.0 or hub_reference_penalty > 0.0:
            reasons.append("demoted as broad hub or harness surface")
        if superficial_penalty > 0:
            reasons.append("demoted for superficial or read-only match")
        if passive_penalty_score > 0:
            reasons.append("passive contextual surface demoted behind owner")
        for detail_reason in list(superficial_detail.get("reasons") or [])[:2]:
            reasons.append(str(detail_reason))
        if selection_lane == "reference_context" and edit_allowed:
            reasons.append("retained as read-only support context")
        if reference_only_guard and candidate_role in {"documentation", "governance_doc", "temporary_artifact"}:
            reasons.append("guarded from edit target promotion")
        if implementation_target_blocked:
            reasons.append("blocked from implementation target promotion")
        reasons = _dedupe_strings(reasons)
        if not reasons:
            reasons.append("retrieval candidate retained by deterministic scoring")

        snippet_candidates: List[Dict[str, Any]] = []
        for line_hit in line_hits:
            snippet_candidates.append(
                {
                    "kind": "line_window",
                    "symbol": None,
                    "start_line": int(line_hit.get("start_line") or 1),
                    "end_line": int(line_hit.get("end_line") or int(line_hit.get("start_line") or 1)),
                    "score": round(0.8 + min(0.15 * len(matched_symbols), 0.3), 3),
                    "reason": "retrieval line hit",
                    "preview": str(line_hit.get("preview") or "")[:240],
                }
            )
        for symbol in sorted(matched_symbols):
            snippet_candidates.append(
                {
                    "kind": "symbol",
                    "symbol": symbol,
                    "start_line": 1,
                    "end_line": 1,
                    "score": 1.0,
                    "reason": "exact symbol match",
                    "preview": symbol,
                }
            )
        deduped_snippets: List[Dict[str, Any]] = []
        seen_snippets: Set[Tuple[Any, ...]] = set()
        for snippet in sorted(snippet_candidates, key=lambda item: (-float(item["score"]), int(item["start_line"]), str(item.get("symbol") or ""))):
            key = (snippet.get("kind"), snippet.get("symbol"), int(snippet.get("start_line") or 0), int(snippet.get("end_line") or 0))
            if key in seen_snippets:
                continue
            seen_snippets.add(key)
            deduped_snippets.append(snippet)
        semantic_rescued = bool(semantic_target_bonus > 0 and targetability_score >= min_file_score and (targetability_score - semantic_target_bonus) < min_file_score)
        score = targetability_score if selection_lane == "implementation_target" else reference_value_score
        ranked.append(
            {
                "path": path,
                "score": score,
                "context_score": reference_value_score,
                "target_score": targetability_score,
                "targetability_score": targetability_score,
                "reference_value_score": reference_value_score,
                "candidate_role": candidate_role,
                "file_role": file_role,
                "bundle_role": None,
                "selection_lane": selection_lane,
                "edit_allowed": edit_allowed,
                "selection_rank": 0,
                "reasons": reasons,
                "hub_kind": hub_kind or None,
                "subsystems": candidate_subsystems,
                "active_subsystems": active_subsystem_hypotheses,
                "subsystem_alignment_score": subsystem_fit_score,
                "graph_score": graph_score,
                "explicit_path_request": bool(explicit_path_request),
                "semantic_score": semantic_score,
                "semantic_bonus": round(max(semantic_target_bonus, semantic_reference_bonus), 6),
                "semantic_rescued": semantic_rescued,
                "centrality_score": centrality_score,
                "ownership_role": ownership_role or None,
                "family_owner_fit": family_owner_fit,
                "candidate_intent_lane": candidate_intent_lane,
                "context_intent_type": context_intent_type or None,
                "structural_family_tags": structural_family_tags,
                "covers": sorted(covers),
                "coverage_score": round(coverage_score, 4) if coverage_score else None,
                "coverage_detail": dict(coverage_detail),
                "passive_relevance": dict(passive_relevance),
                "passive_relevance_penalty": round(passive_penalty_score, 4) if passive_penalty_score else None,
                "superficial_match_penalty": round(superficial_penalty, 4) if superficial_penalty else None,
                "superficial_match_detail": dict(superficial_detail),
                "evidence_sources": list(candidate.get("evidence_sources") or []),
                "snippet_candidates": deduped_snippets,
            }
        )

    if not ranked:
        return {
            "ranked": [],
            "selected": [],
            "implementation_targets": [],
            "validation_targets": [],
            "reference_context": [],
            "excluded_summary": {
                "retrieved_candidates": len(file_candidates),
                "ranked_candidates": 0,
                "dropped_low_signal": len(file_candidates),
                "dropped_duplicates": 0,
            },
        }

    ranked = enforce_actionability_policy(
        ranked,
        task_archetype=_task_archetype(task_profile),
        narrow_task=narrow_task,
        policy=policy_config,
    )
    ranked_lookup: Dict[str, Dict[str, Any]] = {str(item.get("path") or ""): item for item in ranked}

    ranked_targets = sorted(
        [item for item in ranked if item.get("selection_lane") == "implementation_target"],
        key=lambda item: (-float(item.get("targetability_score") or 0.0), item["path"]),
    )
    ranked_refs = sorted(
        [item for item in ranked if item.get("selection_lane") != "implementation_target"],
        key=lambda item: (-float(item.get("reference_value_score") or 0.0), item["path"]),
    )

    selected_targets: List[Dict[str, Any]] = []
    selected_refs: List[Dict[str, Any]] = []
    task_archetype = _task_archetype(task_profile)

    if task_archetype == "repo_assessment":
        seen_roles: Set[str] = set()
        selected: List[Dict[str, Any]] = []
        assessment_ranked = sorted(ranked, key=lambda item: (-float(item.get("reference_value_score") or item.get("score") or 0.0), item["path"]))
        for item in assessment_ranked:
            role = str(item.get("file_role") or "unknown")
            if role not in seen_roles:
                selected.append(dict(item))
                seen_roles.add(role)
            if len(selected) >= max_selected_files:
                break
        for item in assessment_ranked:
            if len(selected) >= max_selected_files:
                break
            if any(str(existing.get("path") or "") == str(item.get("path") or "") for existing in selected):
                continue
            selected.append(dict(item))
        for idx, item in enumerate(selected, start=1):
            item["selection_rank"] = idx
            item["bundle_role"] = item.get("bundle_role") or None
            kept = item["snippet_candidates"][: max(1, min(2, max_snippets - snippet_total))]
            item["snippet_candidates"] = kept
            snippet_total += len(kept)
            if snippet_total >= max_snippets:
                break
        selected = [item for item in selected if item["snippet_candidates"]]
        return {
            "ranked": ranked,
            "selected": selected,
            "implementation_targets": [],
            "validation_targets": [],
            "reference_context": selected,
            "excluded_summary": {
                "retrieved_candidates": len(file_candidates),
                "ranked_candidates": len(ranked),
                "selected_targets": 0,
                "selected_reference_context": len(selected),
                "dropped_low_signal": max(0, len(file_candidates) - len(ranked)),
                "dropped_duplicates": max(0, len(ranked) - len(selected)),
            },
        }

    if task_archetype in {"information_retrieval", "documentation_contract"}:
        selected = [dict(item) for item in ranked_refs[:max_selected_files]]
        if not selected:
            selected = [dict(item) for item in sorted(ranked, key=lambda item: (-float(item.get("reference_value_score") or item.get("score") or 0.0), item["path"]))[:max_selected_files]]
        for idx, item in enumerate(selected, start=1):
            item["selection_rank"] = idx
            kept = item["snippet_candidates"][: max(1, min(2, max_snippets - snippet_total))]
            item["snippet_candidates"] = kept
            snippet_total += len(kept)
            if snippet_total >= max_snippets:
                break
        selected = [item for item in selected if item["snippet_candidates"]]
        return {
            "ranked": ranked,
            "selected": selected,
            "implementation_targets": [],
            "validation_targets": [],
            "reference_context": selected,
            "excluded_summary": {
                "retrieved_candidates": len(file_candidates),
                "ranked_candidates": len(ranked),
                "selected_targets": 0,
                "selected_reference_context": len(selected),
                "dropped_low_signal": max(0, len(file_candidates) - len(ranked)),
                "dropped_duplicates": max(0, len(ranked) - len(selected)),
            },
        }

    primary_impl_targets = [item for item in ranked_targets if str(item.get("candidate_role") or "") in {"implementation_code", "config_file"}]
    secondary_targets = [item for item in ranked_targets if item not in primary_impl_targets]
    best_primary = float(primary_impl_targets[0]["targetability_score"]) if primary_impl_targets else (float(ranked_targets[0]["targetability_score"]) if ranked_targets else 0.0)
    min_primary_keep = min(2 if narrow_task else 4, max_selected_files)
    subsystem_hypotheses = _dedupe_strings(search_plan.get("subsystem_hypotheses") or [])
    anchor_limit = 0
    if _needs_anchor_injection(search_plan):
        desired_anchor_count = 1 if narrow_task or len(subsystem_hypotheses) <= 1 else 2
        anchor_limit = max(1, min(desired_anchor_count, max(0, max_selected_files - 1)))
    target_slot_limit = max_selected_files - anchor_limit if anchor_limit else max_selected_files
    if ranked_targets and target_slot_limit <= 0:
        target_slot_limit = 1

    selected_targets = _select_targets_with_diversity(
        primary_impl_targets + secondary_targets,
        max_slots=target_slot_limit,
        min_primary_keep=min_primary_keep,
        best_primary=best_primary,
        score_margin_keep_secondary=score_margin_keep_secondary,
        narrow_task=narrow_task,
        max_targets_per_subsystem=max_targets_per_subsystem,
    )

    if not selected_targets and task_archetype == "implementation_change":
        fallback_target = _fallback_promotable_target(ranked_targets + ranked_refs, families=families, task_profile=task_profile)
        if fallback_target is not None:
            selected_targets = [fallback_target]
            ranked_targets = [fallback_target] + [item for item in ranked_targets if str(item.get("path") or "") != str(fallback_target.get("path") or "")]

    selected_targets = _rescue_non_scheduler_targets(
        selected_targets=selected_targets,
        ranked_targets=ranked_targets,
        ranked_refs=ranked_refs,
        ranked=ranked,
        families=families,
        task_profile=task_profile,
        query_terms=query_terms,
        target_slot_limit=target_slot_limit,
    )
    selected_targets = _drop_scheduler_targets_when_specific_owners_exist(
        selected_targets=selected_targets,
        families=families,
    )

    if _supports_owner_collapse(families, task_profile) and selected_targets:
        dominant_owner = next((item for item in selected_targets if str(item.get("ownership_role") or "") == "core_owner"), None)
        if dominant_owner is None:
            dominant_owner = next((item for item in ranked_targets if str(item.get("ownership_role") or "") == "core_owner"), None)
        if dominant_owner is not None:
            dominant_owner_score = float(dominant_owner.get("targetability_score") or dominant_owner.get("score") or 0.0)
            dominant_owner_fit = float(dominant_owner.get("family_owner_fit") or 0.0)
            other_owner_scores = [
                float(item.get("targetability_score") or item.get("score") or 0.0)
                for item in ranked_targets
                if str(item.get("path") or "") != str(dominant_owner.get("path") or "")
                and str(item.get("ownership_role") or "") == "core_owner"
            ]
            other_owner_fits = [
                float(item.get("family_owner_fit") or 0.0)
                for item in ranked_targets
                if str(item.get("path") or "") != str(dominant_owner.get("path") or "")
                and str(item.get("ownership_role") or "") == "core_owner"
            ]
            next_owner_score = max(other_owner_scores) if other_owner_scores else -999.0
            next_owner_fit = max(other_owner_fits) if other_owner_fits else -999.0
            collapse_threshold = 1.15 if (_is_truth_contract_family(families, task_profile) or _is_ui_truth_projection_family(families, task_profile) or _is_retrieval_ranking_family(families, task_profile) or _is_scheduler_family(families)) else 1.45
            owner_fit_threshold = 1.4 if not _is_scheduler_family(families) else 1.6
            if dominant_owner_fit >= owner_fit_threshold and (dominant_owner_fit - next_owner_fit >= 0.35 or next_owner_fit <= 0.45) and (dominant_owner_score - next_owner_score >= collapse_threshold or not other_owner_scores):
                displaced_reference_targets: List[Dict[str, Any]] = []
                for candidate in selected_targets:
                    candidate_path = str(candidate.get("path") or "")
                    if not candidate_path or candidate_path == str(dominant_owner.get("path") or ""):
                        continue
                    if _is_validation_rank_candidate(candidate):
                        continue
                    if not _family_surface_categories(candidate, families=families, task_profile=task_profile):
                        continue
                    demoted = dict(candidate)
                    demoted["selection_lane"] = "reference_context"
                    demoted["edit_allowed"] = False
                    displaced_reference_targets.append(demoted)
                collapsed_targets: List[Dict[str, Any]] = [dict(dominant_owner)]
                selected_paths = {str(dominant_owner.get("path") or "")}
                paired_test = (
                    _pick_paired_test(str(dominant_owner.get("path") or ""), ranked, selected_paths)
                    if _should_promote_paired_test_target(
                        ranked_targets=ranked_targets,
                        families=families,
                        task_profile=task_profile,
                    )
                    else None
                )
                if paired_test is not None and len(collapsed_targets) < max(2, target_slot_limit):
                    collapsed_targets.append(paired_test)
                    selected_paths.add(str(paired_test.get("path") or ""))
                selected_targets = collapsed_targets
                if displaced_reference_targets:
                    selected_refs = _dedupe_ranked_entries_by_path(list(displaced_reference_targets) + list(selected_refs))

    include_context_docs = docs_task or docs_reference_preferred or not primary_impl_targets
    max_reference_keep = 2 if narrow_task else max_selected_files
    if _is_retrieval_ranking_family(families, task_profile):
        max_reference_keep = max(max_reference_keep, 3)
    selected_paths = {str(item.get("path") or "") for item in selected_targets}
    anchor_candidates = _pick_anchor_candidates(
        ranked,
        selected_paths=selected_paths,
        subsystem_asset=subsystem_asset,
        subsystem_hypotheses=subsystem_hypotheses,
        anchor_limit=anchor_limit,
        families=families,
        task_profile=task_profile,
        query_terms=query_terms,
    )
    for item in anchor_candidates:
        path = str(item.get("path") or "")
        if not path or path in selected_paths:
            continue
        if len(selected_targets) + len(selected_refs) >= max_selected_files or len(selected_refs) >= max_reference_keep:
            break
        selected_refs.append(dict(item))
        selected_paths.add(path)

    for item in ranked_refs:
        path = str(item.get("path") or "")
        if not path or path in selected_paths:
            continue
        if _should_skip_reference_candidate(path, families=families, task_profile=task_profile, query_terms=query_terms):
            continue
        if len(selected_targets) + len(selected_refs) >= max_selected_files or len(selected_refs) >= max_reference_keep:
            break
        role = str(item.get("candidate_role") or "")
        if role in {"documentation", "governance_doc", "temporary_artifact"} and not include_context_docs:
            if sum(1 for entry in selected_refs if str(entry.get("candidate_role") or "") in {"documentation", "governance_doc", "temporary_artifact"}) >= 1:
                continue
        selected_refs.append(dict(item))
        selected_paths.add(path)

    if truth_contract_focus:
        preferred_doc = _pick_local_contract_doc(query_terms, ranked_refs, selected_paths)
        if preferred_doc is not None:
            doc_path = str(preferred_doc.get("path") or "")
            if doc_path and doc_path not in selected_paths:
                if len(selected_targets) + len(selected_refs) >= max_selected_files or len(selected_refs) >= max_reference_keep:
                    if selected_refs:
                        removed = selected_refs.pop(-1)
                        selected_paths.discard(str(removed.get("path") or ""))
                if len(selected_targets) + len(selected_refs) < max_selected_files and len(selected_refs) < max_reference_keep:
                    selected_refs.insert(0, preferred_doc)
                    selected_paths.add(doc_path)
        selected_refs = [
            item
            for item in selected_refs
            if not str(item.get("path") or "").lower().startswith("infra/pipeline/")
        ]
        selected_refs = sorted(
            selected_refs,
            key=lambda item: (
                0 if str(item.get("ownership_role") or "") == "local_contract_doc" else 1,
                -float(item.get("reference_value_score") or item.get("score") or 0.0),
                str(item.get("path") or ""),
            ),
        )

    selected_targets, selected_refs = _ensure_family_shape(
        selected_targets=selected_targets,
        selected_refs=selected_refs,
        ranked_targets=ranked_targets,
        ranked_refs=ranked_refs,
        max_selected_files=max_selected_files,
        max_reference_keep=max_reference_keep,
        families=families,
        task_profile=task_profile,
    )
    selected_targets, selected_refs = _ensure_retrieval_family_refs(
        selected_targets=selected_targets,
        selected_refs=selected_refs,
        ranked_targets=ranked_targets,
        ranked_refs=ranked_refs,
        max_selected_files=max_selected_files,
        max_reference_keep=max_reference_keep,
        families=families,
        task_profile=task_profile,
    )
    selected_targets, selected_refs = _ensure_mailbox_writeback_seam_targets(
        selected_targets=selected_targets,
        selected_refs=selected_refs,
        ranked_targets=ranked_targets,
        ranked_refs=ranked_refs,
        max_selected_files=max_selected_files,
        mailbox_writeback_seam_focus=mailbox_writeback_seam_focus,
    )
    selected_refs = _rebalance_reference_context(
        selected_refs,
        ranked_refs=ranked_refs,        max_reference_keep=max_reference_keep,
        families=families,
        task_profile=task_profile,
    )
    selected_targets = _apply_stage5_lane_caps(
        selected_targets,
        replacement_pool=ranked_targets,
        max_keep=max(1, target_slot_limit),
        lane_kind="target",
        policy_config=policy_config,
        ranked_lookup=ranked_lookup,
    )
    selected_refs = _apply_stage5_lane_caps(
        selected_refs,
        replacement_pool=ranked_refs,
        max_keep=max_reference_keep,
        lane_kind="reference",
        policy_config=policy_config,
        ranked_lookup=ranked_lookup,
    )
    if mailbox_writeback_seam_focus:
        selected_targets = _sort_mailbox_writeback_entries(selected_targets, target_entries=True)
        selected_refs = _sort_mailbox_writeback_entries(selected_refs, target_entries=False)
    additional_validation_targets = _select_additional_validation_targets(
        selected_targets=selected_targets,
        selected_refs=selected_refs,        ranked_targets=ranked_targets,
        ranked_refs=ranked_refs,
        ranked=ranked,
        job_contract=job_contract,
        task_profile=task_profile,
    )
    if additional_validation_targets:
        required_validation_paths = {
            str(item.get("path") or "")
            for item in additional_validation_targets
            if str(item.get("path") or "")
        }
        selected_refs = _dedupe_ranked_entries_by_path(list(additional_validation_targets) + list(selected_refs))
        while len(selected_targets) + len(selected_refs) > max_selected_files or len(selected_refs) > max_reference_keep:
            removable_index = next(
                (
                    idx
                    for idx in range(len(selected_refs) - 1, -1, -1)
                    if str(selected_refs[idx].get("path") or "") not in required_validation_paths
                ),
                None,
            )
            if removable_index is None:
                break
            selected_refs.pop(removable_index)
        if mailbox_writeback_seam_focus:
            selected_refs = _sort_mailbox_writeback_entries(selected_refs, target_entries=False)

    if retrieval_ranking_focus:
        legacy_retrieval_pairing_mode = bool(            not task_profile.get("planning_profiles")
            and not str(task_profile.get("task_mode") or "").strip()
            and not str(task_profile.get("task_archetype") or "").strip()
        )
        primary_retrieval_owner = next(
            (
                dict(item)
                for item in selected_targets
                if str(item.get("path") or "").endswith("infra/pipeline/planning.py")
            ),
            None,
        )
        if primary_retrieval_owner is None:
            primary_retrieval_owner = next(
                (
                    dict(item)
                    for item in selected_targets
                    if str(item.get("ownership_role") or "") == "core_owner"
                    and str(item.get("path") or "").startswith("infra/pipeline/")
                ),
                None,
            )
        paired_validation_target = None
        if primary_retrieval_owner is not None:
            paired_validation_target = _pick_paired_test(
                str(primary_retrieval_owner.get("path") or ""),
                list(selected_refs) + list(selected_targets) + list(ranked_refs) + list(ranked_targets) + list(ranked),
                set(),
            )
            if paired_validation_target is None:
                paired_validation_target = next(
                    (
                        dict(item)
                        for item in list(selected_refs) + list(selected_targets) + list(ranked_refs) + list(ranked_targets) + list(ranked)
                        if _is_validation_rank_candidate(item)
                        and str(item.get("path") or "").startswith("tests/pipeline/")
                    ),
                    None,
                )
        if primary_retrieval_owner is not None:
            keep_target_paths = {str(primary_retrieval_owner.get("path") or "")}
            collapsed_targets: List[Dict[str, Any]] = [dict(primary_retrieval_owner)]
            if paired_validation_target is not None:
                if legacy_retrieval_pairing_mode:
                    promoted_validation = dict(paired_validation_target)
                    promoted_validation["selection_lane"] = "implementation_target"
                    promoted_validation["edit_allowed"] = True
                    promoted_validation["reasons"] = _dedupe_strings(
                        list(promoted_validation.get("reasons") or [])
                        + ["paired retrieval-ranking validation surface retained with dominant owner"]
                    )
                    collapsed_targets.append(promoted_validation)
                    keep_target_paths.add(str(promoted_validation.get("path") or ""))
            demoted_targets = [
                dict(item)
                for item in selected_targets
                if str(item.get("path") or "") not in keep_target_paths
            ]
            for item in demoted_targets:
                item["selection_lane"] = "reference_context"
                item["edit_allowed"] = False
                item["reasons"] = _dedupe_strings(
                    list(item.get("reasons") or [])
                    + ["demoted behind dominant retrieval owner and paired validation surface"]
                )
            selected_targets = _dedupe_ranked_entries_by_path(collapsed_targets)
            retained_ref_paths = {
                str(item.get("path") or "")
                for item in selected_refs
                if str(item.get("path") or "") not in keep_target_paths
            }
            retained_refs = [
                dict(item)
                for item in selected_refs
                if str(item.get("path") or "") in retained_ref_paths
            ]
            if paired_validation_target is not None and not legacy_retrieval_pairing_mode:
                retained_validation = dict(paired_validation_target)
                retained_validation["selection_lane"] = "reference_context"
                retained_validation["edit_allowed"] = False
                retained_validation["reasons"] = _dedupe_strings(
                    list(retained_validation.get("reasons") or [])
                    + ["paired retrieval-ranking validation surface retained as reference validation context"]
                )
                retained_refs = [retained_validation] + retained_refs
            selected_refs = _dedupe_ranked_entries_by_path(retained_refs + demoted_targets)

    selected = list(selected_targets) + list(selected_refs)
    for idx, item in enumerate(selected, start=1):
        item["selection_rank"] = idx
        lane_limit = (2 if narrow_task else 3) if item.get("selection_lane") == "implementation_target" else 1
        allowed_count = max(1, min(lane_limit, max_snippets - snippet_total))
        kept_snippets = item["snippet_candidates"][:allowed_count]
        item["snippet_candidates"] = kept_snippets
        snippet_total += len(kept_snippets)
        if snippet_total >= max_snippets:
            break
    selected = [item for item in selected if item["snippet_candidates"]]
    selected_target_paths = {str(item.get("path") or "") for item in selected if item.get("selection_lane") == "implementation_target"}
    selected_ref_paths = {str(item.get("path") or "") for item in selected if item.get("selection_lane") != "implementation_target"}
    selected_targets = [item for item in selected if str(item.get("path") or "") in selected_target_paths]
    selected_refs = [item for item in selected if str(item.get("path") or "") in selected_ref_paths]
    selected_validation_targets = [item for item in selected if _is_validation_rank_candidate(item)]
    if truth_contract_focus:
        selected_targets = [
            item for item in selected_targets if not str(item.get("path") or "").lower().startswith("infra/pipeline/")
        ]
        selected_validation_targets = [
            item for item in selected_validation_targets if not str(item.get("path") or "").lower().startswith("infra/pipeline/")
        ]
        selected = list(selected_targets) + list(selected_refs)
    return {
        "ranked": ranked,
        "selected": selected,
        "implementation_targets": selected_targets,
        "validation_targets": _dedupe_ranked_entries_by_path(selected_validation_targets),
        "reference_context": selected_refs,
        "excluded_summary": {
            "retrieved_candidates": len(file_candidates),
            "ranked_candidates": len(ranked),
            "selected_targets": len(selected_targets),
            "selected_reference_context": len(selected_refs),
            "dropped_low_signal": max(0, len(file_candidates) - len(ranked)),
            "dropped_duplicates": max(0, len(ranked) - len(selected)),
        },
    }



def _apply_lane_metadata(entries: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for entry in entries:
        item = dict(entry)
        lane_metadata_for_ranked(item)
        out.append(item)
    return out


def _sync_selected_views(ranked: Sequence[Mapping[str, Any]], selected_paths: Sequence[str]) -> List[Dict[str, Any]]:
    ranked_lookup = {
        str(item.get("path") or ""): dict(item)
        for item in ranked
        if str(item.get("path") or "")
    }
    synced: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for raw_path in selected_paths:
        path = str(raw_path or "")
        if not path or path in seen:
            continue
        ranked_item = ranked_lookup.get(path)
        if ranked_item is None:
            continue
        synced.append(dict(ranked_item))
        seen.add(path)
    return synced


def rank_context(result_obj: Mapping[str, Any], config: Mapping[str, Any]) -> Dict[str, Any]:
    retrieval = dict(result_obj.get("retrieval") or {})
    file_candidates = list(retrieval.get("file_candidates") or [])
    if not file_candidates:
        raise PipelineError("empty_retrieval", "retrieval artifact contains no file candidates", exit_code=5)
    intake_assessment = dict(result_obj.get("intake_assessment") or {})
    job_contract = dict(result_obj.get("job_contract") or {})
    task_profile = dict(result_obj.get("task_profile") or {})
    search_plan = dict(result_obj.get("search_plan") or {})
    search_extracted = _coerce_mapping(result_obj.get("extracted_terms") or search_plan.get("extracted_terms"))
    families = _effective_families(job_contract, task_profile)
    replace_spec = detect_global_replace(job_contract)
    is_global_replace = "global_replace" in list(task_profile.get("secondary_tags") or []) or bool(replace_spec)
    query_terms = _job_terms(job_contract, task_profile)
    changed_hints = {Path(str(x)).name.lower() for x in job_contract.get("issue_context", {}).get("changed_files_hint", [])}
    explicit_file_hints = {Path(str(x)).name.lower() for x in list(search_extracted.get("file_hints") or []) if str(x).strip()}
    failing_tests = " ".join([str(x) for x in job_contract.get("issue_context", {}).get("failing_tests", [])]).lower()
    min_file_score = float(config.get("limits", {}).get("min_file_score", 1))
    semantic_weight = float(config.get("semantic", {}).get("semantic_score_weight") or 0.35)
    max_selected_files = int(config.get("limits", {}).get("max_selected_files", 8))
    max_snippets = int(config.get("limits", {}).get("max_snippets", 20))
    score_margin_keep_secondary = float(config.get("limits", {}).get("score_margin_keep_secondary", 0.15))
    narrow_task = _looks_narrow_task(job_contract, task_profile, families)
    cross_cutting_shape_focus = bool(
        _is_scheduler_family(families)
        or _is_ui_truth_projection_family(families, task_profile)
        or _is_truth_contract_family(families, task_profile)
        or _is_retrieval_ranking_family(families, task_profile)
    )
    if not is_global_replace and narrow_task:
        max_selected_files = min(max_selected_files, 6 if cross_cutting_shape_focus else 4)
        max_snippets = min(max_snippets, 12 if cross_cutting_shape_focus else 8)
        score_margin_keep_secondary = min(score_margin_keep_secondary, 0.08)

    repo_root = str(job_contract.get("repo_root") or "")
    planning_assets = _load_planning_assets(repo_root, config)
    subsystem_asset = _coerce_mapping(planning_assets.get("subsystem_map"))
    max_targets_per_subsystem = max(1, int(config.get("limits", {}).get("max_targets_per_subsystem", DEFAULT_MAX_TARGETS_PER_SUBSYSTEM)))

    semantic_section = dict(retrieval.get("semantic") or {})
    semantic_runtime = dict(semantic_section.get("runtime") or {})
    semantic_status = str(semantic_runtime.get("semantic_status") or semantic_section.get("status") or "disabled")
    effective_mode = str(semantic_runtime.get("effective_mode") or semantic_section.get("mode") or "off")
    requested_mode = str(semantic_runtime.get("requested_mode") or semantic_section.get("requested", {}).get("mode") or config.get("semantic", {}).get("semantic_mode") or "off")
    semantic_bonus_enabled = semantic_status == "ok" and effective_mode in {"retrieve_and_rank", "rank_only"}

    actual_pass = _rank_context_pass(
        file_candidates=file_candidates,
        job_contract=job_contract,
        intake_assessment=intake_assessment,
        task_profile=task_profile,
        search_plan=search_plan,
        planning_assets=planning_assets,
        subsystem_asset=subsystem_asset,
        families=families,
        replace_spec=replace_spec,
        is_global_replace=is_global_replace,
        query_terms=query_terms,
        changed_hints=changed_hints,
        explicit_file_hints=explicit_file_hints,
        failing_tests=failing_tests,
        min_file_score=min_file_score,
        semantic_bonus_enabled=semantic_bonus_enabled,
        semantic_mode=effective_mode,
        semantic_weight=semantic_weight,
        max_selected_files=max_selected_files,
        max_snippets=max_snippets,
        score_margin_keep_secondary=score_margin_keep_secondary,
        narrow_task=narrow_task,
        max_targets_per_subsystem=max_targets_per_subsystem,
        policy_config=dict(config.get("retrieval_policy") or {}),
    )
    if not actual_pass["selected"]:
        raise PipelineError("weak_ranking", "ranking selected files but no snippet anchors remained", exit_code=5)

    deterministic_candidates = [candidate for candidate in file_candidates if "deterministic" in list(candidate.get("evidence_sources") or [])]
    baseline_pool = deterministic_candidates if deterministic_candidates else file_candidates
    baseline_pass = _rank_context_pass(
        file_candidates=baseline_pool,
        job_contract=job_contract,
        intake_assessment=intake_assessment,
        task_profile=task_profile,
        search_plan=search_plan,
        planning_assets=planning_assets,
        subsystem_asset=subsystem_asset,
        families=families,
        replace_spec=replace_spec,
        is_global_replace=is_global_replace,
        query_terms=query_terms,
        changed_hints=changed_hints,
        explicit_file_hints=explicit_file_hints,
        failing_tests=failing_tests,
        min_file_score=min_file_score,
        semantic_bonus_enabled=False,
        semantic_mode="off",
        semantic_weight=semantic_weight,
        max_selected_files=max_selected_files,
        max_snippets=max_snippets,
        score_margin_keep_secondary=score_margin_keep_secondary,
        narrow_task=narrow_task,
        max_targets_per_subsystem=max_targets_per_subsystem,
        policy_config=dict(config.get("retrieval_policy") or {}),
    )

    actual_paths = [str(item.get("path") or "") for item in actual_pass["selected"]]
    baseline_paths = [str(item.get("path") or "") for item in baseline_pass["selected"]]
    actual_target_paths = [str(item.get("path") or "") for item in actual_pass["implementation_targets"]]
    baseline_target_paths = [str(item.get("path") or "") for item in baseline_pass["implementation_targets"]]
    actual_validation_paths = [str(item.get("path") or "") for item in actual_pass.get("validation_targets") or []]
    baseline_validation_paths = [str(item.get("path") or "") for item in baseline_pass.get("validation_targets") or []]
    actual_reference_paths = [str(item.get("path") or "") for item in actual_pass["reference_context"]]
    baseline_reference_paths = [str(item.get("path") or "") for item in baseline_pass["reference_context"]]
    deterministic_path_set = {str(item.get("path") or "") for item in deterministic_candidates}
    semantic_only_selected_paths = [path for path in actual_paths if path not in deterministic_path_set]
    semantic_only_candidate_paths = list(semantic_runtime.get("semantic_only_candidate_paths") or [])
    changed_selected_files = set(actual_paths) != set(baseline_paths)
    changed_ranking_order = actual_paths != baseline_paths
    changed_target_selection = set(actual_target_paths) != set(baseline_target_paths)
    changed_validation_selection = set(actual_validation_paths) != set(baseline_validation_paths)
    changed_reference_selection = set(actual_reference_paths) != set(baseline_reference_paths)
    semantic_rescued_paths = [str(item.get("path") or "") for item in actual_pass["selected"] if bool(item.get("semantic_rescued"))]
    retrieval_strategy = str(retrieval.get("retrieval_provenance", {}).get("strategy") or semantic_runtime.get("effective_strategy") or "deterministic_only")

    actual_ranked = _apply_lane_metadata(actual_pass["ranked"])
    actual_selected = _sync_selected_views(actual_ranked, [str(item.get("path") or "") for item in actual_pass["selected"]])
    actual_targets = _sync_selected_views(actual_ranked, [str(item.get("path") or "") for item in actual_pass["implementation_targets"]])
    actual_validation_targets = _sync_selected_views(actual_ranked, [str(item.get("path") or "") for item in actual_pass.get("validation_targets") or []])
    actual_refs = _sync_selected_views(actual_ranked, [str(item.get("path") or "") for item in actual_pass["reference_context"]])
    rank_diagnostics = compute_rank_diagnostics(actual_ranked, top_k=min(5, len(actual_ranked)))

    ranked_context = {
            "selection_mode": "exhaustive_literal_replace" if is_global_replace else "targeted_ranked_context",
            "replace_spec": replace_spec,
            "implementation_targets": actual_targets,
            "validation_targets": actual_validation_targets,
            "reference_context": actual_refs,
            "selected_files": actual_selected,
            "ranked_files": actual_ranked,
            "lane_views": {
                "target": [item for item in actual_ranked if str(item.get("primary_lane") or "") == "target"],
                "validation": list(actual_validation_targets),
                "support": [item for item in actual_ranked if str(item.get("primary_lane") or "") == "support"],
                "reference": [item for item in actual_ranked if str(item.get("primary_lane") or "") == "reference"],
                "witness": [item for item in actual_ranked if str(item.get("primary_lane") or "") == "witness"],
                "passive": [item for item in actual_ranked if str(item.get("primary_lane") or "") == "passive"],
                "suppressed": [item for item in actual_ranked if str(item.get("primary_lane") or "") == "suppressed"],
            },
            "semantic_ranking": {
                "enabled": semantic_bonus_enabled,
                "requested_mode": requested_mode,
                "effective_mode": effective_mode,
                "requested_provider": semantic_runtime.get("requested_provider"),
                "effective_provider": semantic_runtime.get("effective_provider"),
                "requested_model": semantic_runtime.get("requested_model"),
                "effective_model": semantic_runtime.get("effective_model"),
                "status": semantic_status,
                "disabled_reason": semantic_runtime.get("disabled_reason"),
                "weight": semantic_weight,
                "strategy": retrieval_strategy,
            },
            "semantic_impact": {
                "changed_selected_files": changed_selected_files,
                "changed_ranking_order": changed_ranking_order,
                "changed_ranking_only": (not changed_selected_files) and changed_ranking_order,
                "changed_implementation_targets": changed_target_selection,
                "changed_validation_targets": changed_validation_selection,
                "changed_reference_context": changed_reference_selection,
                "semantic_only_candidate_count": len(semantic_only_candidate_paths),
                "semantic_only_candidate_paths": semantic_only_candidate_paths,
                "semantic_only_selected_count": len(semantic_only_selected_paths),
                "semantic_only_selected_paths": semantic_only_selected_paths,
                "semantic_rescued_count": len(semantic_rescued_paths),
                "semantic_rescued_paths": semantic_rescued_paths,
                "baseline_selected_files": baseline_paths,
                "actual_selected_files": actual_paths,
                "baseline_implementation_targets": baseline_target_paths,
                "actual_implementation_targets": actual_target_paths,
                "baseline_validation_targets": baseline_validation_paths,
                "actual_validation_targets": actual_validation_paths,
                "baseline_reference_context": baseline_reference_paths,
                "actual_reference_context": actual_reference_paths,
            },
            "excluded_summary": actual_pass["excluded_summary"],
            "rank_diagnostics": rank_diagnostics,
        }
    return {
        "intake_assessment": ensure_intake_assessment(intake_assessment),
        "job_contract": job_contract,
        "task_profile": ensure_task_profile_foundation(task_profile),
        "search_plan": ensure_search_plan_foundation(search_plan),
        "retrieval": retrieval,
        "ranked_context": ensure_ranked_context_foundation(ranked_context),
    }

def _extract_symbol_window(lines: List[str], symbol: str) -> Optional[Tuple[int, int]]:
    symbol_lower = symbol.lower()
    start_idx: Optional[int] = None
    indent_level = 0
    for idx, line in enumerate(lines):
        match = FUNC_RE.match(line)
        if match and symbol_lower in line.lower():
            start_idx = idx
            indent_level = len(match.group(1))
            break
    if start_idx is None:
        return None
    end_idx = len(lines) - 1
    for idx in range(start_idx + 1, len(lines)):
        match = FUNC_RE.match(lines[idx])
        if match and len(match.group(1)) <= indent_level:
            end_idx = idx - 1
            break
    return (start_idx + 1, end_idx + 1)


def _import_lines(lines: List[str], max_lines: int = 12) -> List[Tuple[int, str]]:
    out: List[Tuple[int, str]] = []
    for idx, line in enumerate(lines[:200], start=1):
        if IMPORT_RE.match(line.strip()):
            out.append((idx, line))
            if len(out) >= max_lines:
                break
    return out


def _snippet_id(path: str, start_line: int, end_line: int, index: int) -> str:
    return f"{path}::{start_line}-{end_line}::{index}"


def _header_context(lines: List[str], max_lines: int = 12) -> Optional[Dict[str, Any]]:
    imports = _import_lines(lines, max_lines=max_lines)
    if not imports:
        return None
    return {
        "kind": "header_context",
        "start_line": int(imports[0][0]),
        "end_line": int(imports[-1][0]),
        "content": "\n".join(line for _, line in imports),
    }


def _bundle_role(selected: Mapping[str, Any], *, lane_rank: int, max_primary_targets: int, families: Sequence[str], task_profile: Mapping[str, Any]) -> Tuple[str, bool]:
    file_role = str(selected.get("file_role") or classify_file_role(str(selected.get("path") or ""), ""))
    return derive_bundle_role(
        _task_archetype(task_profile),
        file_role=file_role,
        selection_lane=str(selected.get("selection_lane") or "reference_context"),
        edit_allowed=bool(selected.get("edit_allowed")),
        lane_rank=lane_rank,
        max_primary_targets=max_primary_targets,
        ownership_role=str(selected.get("ownership_role") or ""),
    )


def _is_broad_reference_hub(entry: Mapping[str, Any]) -> bool:
    hub_kind = str(entry.get("hub_kind") or "")
    if not hub_kind:
        return False
    ownership_role = str(entry.get("ownership_role") or "")
    explicit_path_request = bool(entry.get("explicit_path_request"))
    owner_signal_strength = float(entry.get("owner_signal_strength") or 0.0)
    hub_surface_penalty = float(entry.get("hub_surface_penalty") or 0.0)
    if explicit_path_request or ownership_role in {"core_owner", "test_surface"}:
        return False
    return hub_surface_penalty >= 0.9 and owner_signal_strength < 1.0


def _reference_selection_priority(entry: Mapping[str, Any], *, families: Sequence[str], task_profile: Mapping[str, Any]) -> Tuple[int, int, float, str]:
    categories = _family_surface_categories(entry, families=families, task_profile=task_profile)
    broad_hub = _is_broad_reference_hub(entry)
    ownership_role = str(entry.get("ownership_role") or "")
    candidate_role = str(entry.get("candidate_role") or "")
    if categories:
        bucket = 0
    elif ownership_role in {"test_surface", "local_contract_doc"}:
        bucket = 1
    elif candidate_role == "test_file":
        bucket = 2
    elif broad_hub:
        bucket = 5
    elif str(entry.get("hub_kind") or ""):
        bucket = 4
    else:
        bucket = 3
    return (bucket, 0 if broad_hub else 1, -float(entry.get("reference_value_score") or entry.get("score") or 0.0), str(entry.get("path") or ""))


def _rebalance_reference_context(
    refs: Sequence[Mapping[str, Any]],
    *,
    ranked_refs: Sequence[Mapping[str, Any]],
    max_reference_keep: int,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    selected = [dict(item) for item in refs]
    if not selected:
        return selected
    selected.sort(key=lambda item: _reference_selection_priority(item, families=families, task_profile=task_profile))
    protected_categories = {
        category
        for item in selected
        for category in _family_surface_categories(item, families=families, task_profile=task_profile)
    }
    hub_count = sum(1 for item in selected if _is_broad_reference_hub(item))
    if hub_count <= 1:
        return selected[:max_reference_keep]
    pool = [dict(item) for item in ranked_refs]
    selected_paths = {str(item.get("path") or "") for item in selected}
    replacement_pool = [
        item
        for item in pool
        if str(item.get("path") or "") not in selected_paths
        and not _is_broad_reference_hub(item)
    ]
    replacement_pool.sort(key=lambda item: _reference_selection_priority(item, families=families, task_profile=task_profile))
    kept: List[Dict[str, Any]] = []
    broad_hub_kept = 0
    for item in selected:
        is_broad_hub = _is_broad_reference_hub(item)
        if is_broad_hub and broad_hub_kept >= 1:
            replacement = None
            for idx, candidate in enumerate(replacement_pool):
                candidate_categories = _family_surface_categories(candidate, families=families, task_profile=task_profile)
                if candidate_categories.difference(protected_categories) or candidate_categories or not str(candidate.get("hub_kind") or ""):
                    replacement = replacement_pool.pop(idx)
                    protected_categories.update(candidate_categories)
                    break
            if replacement is not None:
                kept.append(dict(replacement))
            continue
        kept.append(dict(item))
        if is_broad_hub:
            broad_hub_kept += 1
    kept.sort(key=lambda item: _reference_selection_priority(item, families=families, task_profile=task_profile))
    return kept[:max_reference_keep]


def _augment_prioritized_bundle_files(
    *,
    prioritized_files: Sequence[Mapping[str, Any]],
    ranked_files: Sequence[Mapping[str, Any]],
    families: Sequence[str],
    task_profile: Mapping[str, Any],
    max_bundle_files: int,
) -> List[Dict[str, Any]]:
    out = [dict(item) for item in prioritized_files]
    if not out:
        return out
    requirements = _family_shape_requirements(families, task_profile)
    if not requirements:
        return out[:max_bundle_files]
    selected_paths = {str(item.get("path") or "") for item in out}
    selected_categories = {
        category
        for item in out
        for category in _family_surface_categories(item, families=families, task_profile=task_profile)
    }
    ranked_pool = [dict(item) for item in ranked_files]
    for category, preferred_kind in requirements:
        if category in selected_categories:
            continue
        candidates = [
            item for item in ranked_pool
            if str(item.get("path") or "") not in selected_paths
            and category in _family_surface_categories(item, families=families, task_profile=task_profile)
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda item: _family_shape_priority(item, category=category), reverse=True)
        candidate = dict(candidates[0])
        if preferred_kind == "reference":
            candidate["selection_lane"] = "reference_context"
            candidate["edit_allowed"] = False
        out.append(candidate)
        selected_paths.add(str(candidate.get("path") or ""))
        selected_categories.add(category)
    if len(out) > max_bundle_files:
        retained = out[:]
        while len(retained) > max_bundle_files:
            removable_index = next(
                (
                    idx for idx in range(len(retained) - 1, -1, -1)
                    if not _family_surface_categories(retained[idx], families=families, task_profile=task_profile)
                ),
                None,
            )
            if removable_index is None:
                break
            retained.pop(removable_index)
        out = retained[:max_bundle_files]
    return out[:max_bundle_files]


def bundle_context(result_obj: Mapping[str, Any], config: Mapping[str, Any]) -> Dict[str, Any]:
    intake_assessment = dict(result_obj.get("intake_assessment") or {})
    ranked_context = dict(result_obj.get("ranked_context") or {})
    implementation_targets = list(ranked_context.get("implementation_targets") or [])
    implementation_target_paths = {
        str(item.get("path") or "")
        for item in implementation_targets
        if str(item.get("path") or "")
    }
    validation_targets = list(ranked_context.get("validation_targets") or [])
    if not validation_targets:
        validation_targets = _dedupe_ranked_entries_by_path(
            [
                dict(item)
                for item in list(implementation_targets)
                + list(ranked_context.get("reference_context") or [])
                + list(ranked_context.get("selected_files") or [])
                if _is_validation_rank_candidate(item)
            ]
        )
    validation_target_paths = {
        str(item.get("path") or "")
        for item in validation_targets
        if str(item.get("path") or "")
    }
    reference_candidates = [
        item
        for item in list(ranked_context.get("reference_context") or [])
        if str(item.get("path") or "") not in validation_target_paths
    ]
    ranked_files = list(ranked_context.get("ranked_files") or [])
    selected_files = list(ranked_context.get("selected_files") or _dedupe_ranked_entries_by_path(list(implementation_targets) + list(validation_targets) + list(reference_candidates)))
    if not selected_files:
        raise PipelineError("weak_ranking", "context bundle requires ranked files", exit_code=5)
    job_contract = dict(result_obj.get("job_contract") or {})
    explicit_test_edit_request = _objective_requests_test_edits(job_contract)
    task_profile = dict(result_obj.get("task_profile") or {})
    families = _effective_families(job_contract, task_profile)
    repo_root = Path(str(job_contract.get("repo_root") or "")).resolve()
    if not repo_root.exists():
        raise PipelineError("invalid_repository", "repo_root missing during context bundling", details={"repo_root": str(repo_root)}, exit_code=4)
    limits = dict(config.get("limits") or {})
    replace_spec = dict((ranked_context.get("replace_spec") or {}))
    selection_mode = str(ranked_context.get("selection_mode") or "targeted_ranked_context")
    is_global_replace = selection_mode == "exhaustive_literal_replace"
    mailbox_writeback_seam_focus = detect_mailbox_writeback_seam_focus(
        job_contract,
        task_profile=task_profile,
        search_plan=dict(result_obj.get("search_plan") or {}),
    )
    max_bundle_files = len(selected_files) if is_global_replace else int(limits.get("max_bundle_files", 6))
    max_bundle_snippets = max(int(limits.get("max_bundle_snippets", 16)), len(selected_files)) if is_global_replace else int(limits.get("max_bundle_snippets", 16))
    max_snippet_chars = int(limits.get("max_snippet_chars", 2500))
    max_total_chars = int(limits.get("max_total_context_chars", 40000))
    max_primary_targets = int(limits.get("max_primary_targets", 4))
    planning_assets = _load_planning_assets(repo_root, config)
    narrow_task = _looks_narrow_task(job_contract, task_profile, families)
    cross_cutting_shape_focus = bool(
        _is_scheduler_family(families)
        or _is_ui_truth_projection_family(families, task_profile)
        or _is_truth_contract_family(families, task_profile)
        or _is_retrieval_ranking_family(families, task_profile)
    )
    if narrow_task and not is_global_replace:
        max_bundle_files = min(max_bundle_files, 6 if cross_cutting_shape_focus else 4)
        max_bundle_snippets = min(max_bundle_snippets, 12 if cross_cutting_shape_focus else 8)
        max_primary_targets = min(max_primary_targets, 3 if cross_cutting_shape_focus else 2)

    prioritized_files = _dedupe_ranked_entries_by_path(list(implementation_targets) + list(validation_targets) + list(reference_candidates))
    if not prioritized_files:
        prioritized_files = list(selected_files)
    reference_candidates = _rebalance_reference_context(
        reference_candidates,
        ranked_refs=ranked_files or selected_files,
        max_reference_keep=max_bundle_files,
        families=families,
        task_profile=task_profile,
    )
    prioritized_files = _dedupe_ranked_entries_by_path(list(implementation_targets) + list(validation_targets) + list(reference_candidates))
    prioritized_files = _augment_prioritized_bundle_files(
        prioritized_files=prioritized_files,
        ranked_files=ranked_files or selected_files,
        families=families,
        task_profile=task_profile,
        max_bundle_files=max_bundle_files,
    )
    bundle_selection = select_bundle_candidates(
        prioritized_files,
        task_archetype=_task_archetype(task_profile),
        max_bundle_files=max_bundle_files,
        policy_config=dict(config.get("retrieval_policy") or {}),
        narrow_task=narrow_task,
    )
    prioritized_files = list(bundle_selection.get("selected_entries") or [])
    prioritized_files = _ensure_mailbox_writeback_bundle_shape(
        selected_entries=prioritized_files,
        candidate_pool=list(implementation_targets) + list(validation_targets) + list(reference_candidates) + list(ranked_files or selected_files),
        max_bundle_files=max_bundle_files,
        mailbox_writeback_seam_focus=mailbox_writeback_seam_focus,
    )
    provisional_bundle_roles = {
        str(item.get("path") or ""): str(item.get("bundle_role") or "")
        for item in prioritized_files        if str(item.get("path") or "")
    }
    anti_collapse_actions: List[str] = [str(item) for item in list(bundle_selection.get("anti_collapse_actions") or []) if str(item).strip()]
    bundle_recovery_hints: List[str] = [str(item) for item in list(bundle_selection.get("recovery_hints") or []) if str(item).strip()]

    files_out: List[Dict[str, Any]] = []
    editable_targets: List[Dict[str, Any]] = []
    read_only_context: List[Dict[str, Any]] = []
    truncation_notes: List[str] = []
    weakness_reasons: List[str] = []
    total_chars = 0
    total_snippets = 0
    editable_lane_rank = 0
    broad_reference_hubs_used = 0

    for selected in prioritized_files:
        rel_path = normalize_repo_relative_path(repo_root, str(selected.get("path") or ""))
        abs_path = repo_root / rel_path
        if not abs_path.exists() or not abs_path.is_file():
            continue
        raw_lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
        header_context = _header_context(raw_lines)
        lane = str(selected.get("selection_lane") or "reference_context")
        explicit_test_edit_target = bool(
            explicit_test_edit_request
            and rel_path in validation_target_paths
            and _is_validation_rank_candidate(selected)
        )
        implicit_paired_test_target = bool(
            rel_path in implementation_target_paths
            and rel_path in validation_target_paths
            and _is_validation_rank_candidate(selected)
            and _supports_paired_test_editing(families, task_profile)
        )
        if explicit_test_edit_target:
            lane = "implementation_target"
        elif implicit_paired_test_target:
            lane = "implementation_target"
        if lane != "implementation_target" and _is_broad_reference_hub(selected):
            if broad_reference_hubs_used >= 1:
                continue
            broad_reference_hubs_used += 1
        per_file_snippet_limit = 3 if lane == "implementation_target" else 1
        snippets: List[Dict[str, Any]] = []
        for candidate in list(selected.get("snippet_candidates") or []):
            if total_snippets >= max_bundle_snippets or len(snippets) >= per_file_snippet_limit:
                break
            symbol = str(candidate.get("symbol") or "").strip()
            window = _extract_symbol_window(raw_lines, symbol) if symbol else None
            if window is None:
                start_line = max(1, int(candidate.get("start_line") or 1) - 30)
                end_line = min(len(raw_lines), int(candidate.get("end_line") or start_line) + 30)
            else:
                start_line, end_line = window
            content = "\n".join(raw_lines[start_line - 1 : end_line])
            if len(content) > max_snippet_chars:
                content = content[: max_snippet_chars - 20] + "\n# ... truncated ..."
                truncation_notes.append(f"Truncated snippet in {rel_path} to max_snippet_chars")
            if total_chars + len(content) > max_total_chars:
                truncation_notes.append(f"Excluded additional snippet from {rel_path} due to total char budget")
                break
            snippet_id = _snippet_id(rel_path, start_line, end_line, len(snippets) + 1)
            snippets.append(
                {
                    "snippet_id": snippet_id,
                    "kind": str(candidate.get("kind") or "line_window"),
                    "snippet_role": "primary_edit" if lane == "implementation_target" and not snippets else ("support_context" if lane == "implementation_target" else "reference_context"),
                    "symbol": symbol or None,
                    "start_line": start_line,
                    "end_line": end_line,
                    "allowed_edit_region": {"start_line": start_line, "end_line": end_line} if lane == "implementation_target" and bool(selected.get("edit_allowed")) else None,
                    "edit_priority": "high" if lane == "implementation_target" and not snippets else ("medium" if lane == "implementation_target" else "low"),
                    "content": content,
                    "selected_because": str(candidate.get("reason") or "ranked snippet"),
                }
            )
            total_chars += len(content)
            total_snippets += 1
        if not snippets and header_context:
            snippets.append(
                {
                    "snippet_id": _snippet_id(rel_path, int(header_context["start_line"]), int(header_context["end_line"]), 1),
                    "kind": "header_context",
                    "snippet_role": "reference_context",
                    "symbol": None,
                    "start_line": int(header_context["start_line"]),
                    "end_line": int(header_context["end_line"]),
                    "allowed_edit_region": None,
                    "edit_priority": "low",
                    "content": str(header_context["content"]),
                    "selected_because": "import/header context",
                }
            )
            total_chars += len(str(header_context["content"]))
            total_snippets += 1
        if not snippets:
            continue

        forced_edit_allowed = bool(explicit_test_edit_target or implicit_paired_test_target)
        if lane == "implementation_target" and (bool(selected.get("edit_allowed")) or forced_edit_allowed):
            editable_lane_rank += 1
        role, effective_edit_allowed = _bundle_role(selected, lane_rank=editable_lane_rank, max_primary_targets=max_primary_targets, families=families, task_profile=task_profile)
        if role in {"primary_target", "secondary_target", "read_only_context", "paired_test", "config_target", "reference_context", "read_only_support"}:
            role = classify_bundle_role(selected, task_archetype=_task_archetype(task_profile), target_index=max(0, editable_lane_rank - 1))
        provisional_role = provisional_bundle_roles.get(rel_path) or provisional_bundle_roles.get(str(selected.get("path") or ""))
        if provisional_role and not explicit_test_edit_target and role in {
            "primary_target",
            "secondary_target",
            "config_target",
            "support_context",
            "contract_context",
            "validation_context",
            "paired_test",
            "witness_only",
            "fallback_hub",
        }:
            role = provisional_role
        if explicit_test_edit_target:
            role = "paired_test"
            effective_edit_allowed = True
        elif implicit_paired_test_target and role in {"validation_context", "witness_only", "support_context"}:
            role = "paired_test"
            effective_edit_allowed = True
        if role not in EDITABLE_ROLES:
            effective_edit_allowed = False
        if (explicit_test_edit_target or implicit_paired_test_target) and role in EDITABLE_ROLES:
            effective_edit_allowed = True
        coverage_detail = dict(selected.get("coverage_detail") or {})
        matched_failure_categories = [str(item) for item in list(coverage_detail.get("matched_failure_categories") or [])]
        file_entry = {
            "path": rel_path,
            "role": role,
            "bundle_role": role,
            "file_role": str(selected.get("file_role") or classify_file_role(rel_path, "")),
            "candidate_role": str(selected.get("candidate_role") or "other"),
            "ownership_role": str(selected.get("ownership_role") or ""),
            "candidate_intent_lane": str(selected.get("candidate_intent_lane") or "").strip() or None,
            "context_intent_type": str(selected.get("context_intent_type") or "").strip() or None,
            "selection_lane": lane,
            "edit_allowed": bool(effective_edit_allowed),
            "selection_score": selected.get("score"),
            "targetability_score": selected.get("targetability_score"),
            "reference_value_score": selected.get("reference_value_score"),
            "semantic_rescued": bool(selected.get("semantic_rescued")),
            "selection_reasons": list(selected.get("reasons") or []),
            "responsibility": sorted({str(item) for item in list(selected.get("covers") or []) if str(item).strip()}),
            "covers_failure_modes": matched_failure_categories,
            "subsystems": [str(item) for item in list(selected.get("subsystems") or []) if str(item).strip()],
            "coverage_detail": coverage_detail,
            "passive_relevance": dict(selected.get("passive_relevance") or {}),
            "ownership_confidence": {
                "score": round(min(1.0, max(0.0, 0.35 + max(float(selected.get("family_owner_fit") or 0.0), 0.0) * 0.22 + (0.28 if str(selected.get("ownership_role") or "") == "core_owner" else 0.0))), 4),
                "notes": _dedupe_strings(list(selected.get("reasons") or [])[:3]),
            },
            "header_context": header_context,
            "snippets": snippets,
        }
        files_out.append(file_entry)
        if effective_edit_allowed and role in EDITABLE_ROLES:
            editable_targets.append(file_entry)
        else:
            read_only_context.append(file_entry)

    if not files_out:
        raise PipelineError("context_bundle_empty", "context bundle could not extract any snippets", exit_code=5)

    bundled_validation_targets = [
        dict(entry)
        for entry in files_out
        if str(entry.get("path") or "") in validation_target_paths
    ]

    composition = evaluate_bundle_composition(_task_archetype(task_profile), files_out)
    weakness_reasons.extend(str(item) for item in list(composition.get("bundle_weakness_reasons") or []))
    if truncation_notes:
        weakness_reasons.append("Context budget forced truncation or omission of some snippets")

    base_bundle_ready = bool(composition.get("bundle_ready_for_task"))
    base_bundle_confidence = float(composition.get("bundle_confidence") or (0.62 if editable_targets else 0.28))
    coverage_validation = _build_bundle_coverage_validation(
        files=files_out,
        composition=composition,
        intake_assessment=intake_assessment,
        task_profile=task_profile,
        search_plan=dict(result_obj.get("search_plan") or {}),
        families=families,
        assets=planning_assets,
        bundle_confidence=base_bundle_confidence,
    )
    weakness_reasons.extend(str(item) for item in list(coverage_validation.get("weakness_reasons") or []))
    diversity_score = float(coverage_validation.get("bundle_diversity_score") or 0.0)
    coverage_score = float(coverage_validation.get("bundle_coverage_score") or 0.0)
    confidence_adjustment = min(0.08, diversity_score * 0.05 + coverage_score * 0.04)
    if coverage_validation.get("warning_markers"):
        confidence_adjustment -= min(0.18, 0.04 * len(list(coverage_validation.get("warning_markers") or [])))
    bundle_confidence = max(0.0, min(0.98, base_bundle_confidence + confidence_adjustment))
    warning_markers = {str(item) for item in list(coverage_validation.get("warning_markers") or []) if str(item).strip()}
    critical_warning_markers = warning_markers.intersection({
        "missing_core_owner",
        "missing_test_surface",
        "missing_projection_owner",
        "missing_projection_ui",
        "missing_truth_boundary",
        "missing_truth_shape",
        "missing_ranking_tests",
        "missing_retrieval_shape",
        "missing_scheduler_recovery",
        "missing_scheduler_ui",
    })
    bundle_ready_for_execution = bool(base_bundle_ready and not (task_profile.get("task_archetype") == "implementation_change" and critical_warning_markers))
    if warning_markers and task_profile.get("task_archetype") == "implementation_change":
        bundle_ready_for_execution = bool(bundle_ready_for_execution and editable_targets)

    context_bundle = {
            "files": files_out,
            "editable_targets": editable_targets,
            "validation_targets": bundled_validation_targets,
            "read_only_context": read_only_context,
            "planning_prompt_material": {
                "issue_summary": str(job_contract.get("objective") or job_contract.get("description") or job_contract.get("title") or ""),
                "constraints": [str(x) for x in job_contract.get("constraints", [])],
                "acceptance_criteria": [str(x) for x in job_contract.get("acceptance_criteria", [])],
                "replace_spec": replace_spec or None,
                "selection_mode": selection_mode,
                "task_archetype": str(task_profile.get("task_archetype") or "implementation_change"),
                "output_contract_mode": str(task_profile.get("output_contract_mode") or "patch_contract"),
                "editable_target_paths": [str(entry.get("path") or "") for entry in editable_targets],
                "validation_target_paths": [str(entry.get("path") or "") for entry in bundled_validation_targets],
                "read_only_context_paths": [str(entry.get("path") or "") for entry in read_only_context],
            },
            "bundle_metrics": {
                "file_count": len(files_out),
                "editable_target_count": len(editable_targets),
                "validation_target_count": len(bundled_validation_targets),
                "read_only_context_count": len(read_only_context),
                "snippet_count": total_snippets,
                "total_chars": total_chars,
                "truncated": bool(truncation_notes),
            },
            "bundle_confidence": round(bundle_confidence, 3),
            "bundle_ready_for_execution": bool(bundle_ready_for_execution),
            "bundle_ready_for_task": bool(bundle_ready_for_execution),
            "bundle_role_counts": dict(composition.get("role_counts") or {}),
            "file_role_counts": dict(composition.get("file_role_counts") or {}),
            "bundle_gap_warning": bool(coverage_validation.get("warning_markers") or weakness_reasons),
            "bundle_gap_details": _dedupe_strings(weakness_reasons),
            "coverage_validation": dict(coverage_validation),
            "bundle_warning_markers": list(coverage_validation.get("warning_markers") or []),
            "bundle_weakness_reasons": _dedupe_strings(weakness_reasons),
            "truncation_notes": truncation_notes,
        }
    bundle_summary = summarize_bundle(
        files_out,
        anti_collapse_actions,
        task_archetype=_task_archetype(task_profile),
        candidate_pool=prioritized_files,
        recovery_hints=bundle_recovery_hints,
    )
    context_bundle["bundle_summary"] = bundle_summary
    context_bundle["owner_uncertainty_flag"] = bool(bundle_summary.get("owner_uncertainty_flag"))
    context_bundle["owner_uncertainty_reason"] = str(bundle_summary.get("owner_uncertainty_reason") or "")
    context_bundle["bundle_quality_status"] = str(bundle_summary.get("bundle_quality_status") or "normal")
    context_bundle["anti_collapse_actions_applied"] = list(bundle_summary.get("anti_collapse_actions_applied") or [])
    context_bundle["bundle_recovery_hints"] = list(bundle_summary.get("bundle_recovery_hints") or [])
    context_bundle["bundle_strategy"] = str(bundle_summary.get("bundle_strategy") or _task_archetype(task_profile))
    context_bundle["bundle_diagnostics"] = compute_bundle_diagnostics(files_out, bundle_summary=bundle_summary)
    if context_bundle["bundle_quality_status"] != "normal":
        context_bundle["bundle_ready_for_execution"] = False
        context_bundle["bundle_ready_for_task"] = False
        context_bundle["bundle_gap_warning"] = True
        context_bundle["bundle_gap_details"] = _dedupe_strings(list(context_bundle.get("bundle_gap_details") or []) + list(context_bundle.get("bundle_recovery_hints") or []))

    return {
        "intake_assessment": ensure_intake_assessment(intake_assessment),
        "job_contract": job_contract,
        "task_profile": ensure_task_profile_foundation(task_profile),
        "ranked_context": ensure_ranked_context_foundation(ranked_context),
        "context_bundle": ensure_context_bundle_foundation(context_bundle),
    }



def build_patch_plan_prompt(bundle_obj: Mapping[str, Any]) -> str:
    compact = {
        "job_contract": bundle_obj.get("job_contract"),
        "task_profile": bundle_obj.get("task_profile"),
        "context_bundle": bundle_obj.get("context_bundle"),
    }
    output_mode = str(_coerce_mapping(bundle_obj.get("task_profile")).get("output_contract_mode") or "patch_contract")
    return (
        "Return JSON only. Do not include markdown, prose, or code patches. "
        "Always produce patch_plan fields: objective, modify_files, non_goals, assumptions, unresolved_uncertainties, "
        "required_tests, planning_confidence, should_escalate, output_contract_mode. "
        f"The requested output_contract_mode is {output_mode}. "
        "Only files listed in context_bundle.editable_targets with edit_allowed=true may appear in modify_files. "
        "For design, assessment, or retrieval tasks you may leave modify_files empty and instead populate the matching mode-specific contract fields. "
        "Files with read-only bundle roles must remain non-editable.\n\n"
        + dumps_json(compact)
    )


def dumps_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _sanitize_patch_plan_against_bundle(bundle_obj: Mapping[str, Any], patch_plan: Mapping[str, Any], warnings: List[str]) -> Dict[str, Any]:
    out = dict(patch_plan)
    bundle_files = list(bundle_obj.get("context_bundle", {}).get("files") or [])
    role_by_path = {str(entry.get("path") or ""): str(entry.get("role") or "") for entry in bundle_files}
    editable_by_path = {str(entry.get("path") or ""): bool(entry.get("edit_allowed")) for entry in bundle_files}
    filtered_modify_files: List[Dict[str, Any]] = []
    dropped_paths: List[str] = []
    for entry in list(out.get("modify_files") or []):
        item = dict(entry or {})
        path = str(item.get("path") or "")
        if not path:
            continue
        if role_by_path.get(path) in EDITABLE_ROLES and editable_by_path.get(path, False):
            filtered_modify_files.append(item)
        else:
            dropped_paths.append(path)
    if dropped_paths:
        warnings.append("model patch plan referenced non-editable targets; sanitized to respect context bundle roles")
        read_only = [str(x) for x in list(out.get("files_read_only_context") or [])]
        seen = set(read_only)
        for path in dropped_paths:
            if path not in seen:
                read_only.append(path)
                seen.add(path)
        out["files_read_only_context"] = read_only
    out["modify_files"] = filtered_modify_files
    if not filtered_modify_files:
        out["should_escalate"] = True
        out["plan_status"] = "INSUFFICIENT_CONTEXT"
        unresolved = [str(x) for x in list(out.get("unresolved_uncertainties") or [])]
        message = "No editable targets remained after bundle-role sanitization"
        if message not in unresolved:
            unresolved.append(message)
        out["unresolved_uncertainties"] = unresolved
    return out


def _change_type_for_entry(file_entry: Mapping[str, Any]) -> str:
    role = str(file_entry.get("candidate_role") or "")
    if role == "config_file":
        return "config_edit"
    if role == "test_file":
        return "test_edit"
    if role in {"documentation", "governance_doc"}:
        return "doc_edit"
    return "code_edit"


def _default_edit_intent(path: str, *, candidate_role: str, families: Sequence[str], replace_spec: Mapping[str, Any], is_global_replace: bool) -> List[str]:
    rel = str(path or "").lower()
    name = Path(path).name
    family_set = _family_alias_set(families)
    if is_global_replace and replace_spec:
        return [f"replace literal '{replace_spec.get('old')}' with '{replace_spec.get('new')}' in {name} after review of exact occurrences"]
    if _is_scheduler_family(families):
        if "scheduler_watchdog" in rel:
            return ["harden stale-lock, orphaned-run, and watchdog recovery behavior", "tighten self-heal guardrails for operator and process failures"]
        if "scheduler_pipeline_healer" in rel or "scheduler_recovery_assistant" in rel:
            return ["strengthen automated recovery decisions and failure classification", "prevent repeated or unsafe recovery actions"]
        if "scheduler_schedule_job" in rel or "scheduler_start_job" in rel or "scheduler_postpone_active_job" in rel:
            return ["tighten manual/operator-triggered scheduling flows", "improve pause, resume, cancel, and retry safety checks"]
        if candidate_role == "config_file":
            return [f"adjust scheduler-related safety configuration in {name}"]
        if candidate_role == "test_file":
            return ["add or adjust regression coverage for scheduler failure and recovery cases"]
        return [f"inspect and update logic in {name} to improve scheduler robustness and failure handling"]
    if "tool_spec_implementation" in family_set:
        return [f"implement execution-target separation and planning contract updates in {name}"]
    if "deterministic_patch_helper" in family_set:
        return [f"implement deterministic patch-helper behavior in {name}"]
    return [f"inspect and update logic in {name} to satisfy objective"]


def _execution_readiness_status(*, editable_files: Sequence[Mapping[str, Any]], task_profile: Mapping[str, Any], families: Sequence[str], warnings: Sequence[str]) -> str:
    if not editable_files:
        return "NEEDS_MORE_CONTEXT"
    if not any(str(entry.get("candidate_role") or "") in {"implementation_code", "config_file"} for entry in editable_files):
        return "NEEDS_MORE_CONTEXT"
    if any(str(entry.get("candidate_role") or "") == "test_file" for entry in editable_files) and not any(
        str(entry.get("candidate_role") or "") in {"implementation_code", "config_file"}
        and str(entry.get("ownership_role") or "") == "core_owner"
        for entry in editable_files
    ):
        return "NEEDS_MORE_CONTEXT"
    if _is_scheduler_family(families) and len(editable_files) > 4:
        return "HIGH_RISK"
    if float(task_profile.get("ambiguity_score") or 0.0) >= 0.55 or len(editable_files) > 3 or warnings:
        return "NEEDS_OPERATOR_DECISION"
    return "READY_FOR_EXECUTION"


def _is_owner_modify_candidate(entry: Mapping[str, Any]) -> bool:
    return bool(
        str(entry.get("candidate_role") or "") in {"implementation_code", "config_file"}
        and str(entry.get("ownership_role") or "") == "core_owner"
    )


def _is_test_modify_candidate(entry: Mapping[str, Any]) -> bool:
    return bool(
        str(entry.get("candidate_role") or "") == "test_file"
        or str(entry.get("ownership_role") or "") == "test_surface"
    )


def _default_required_tests_for_contract(
    *,
    families: Sequence[str],
    task_profile: Mapping[str, Any],
    truth_contract_focus: bool,
    orchestration_focus: bool,
    ui_projection_focus: bool,
    retrieval_ranking_focus: bool,
    scheduler_focus: bool,
) -> List[str]:
    if orchestration_focus:
        return [
            "crash-window checkpoint regression coverage",
            "started-before-side-effect and completed-before-advance tests",
            "duplicate replay suppression after restart",
        ]
    if scheduler_focus:
        return [
            "scheduler recovery regression coverage",
            "watchdog and stale-claim regression checks",
            "pause or deadlock classification regression coverage",
        ]
    if ui_projection_focus:
        return [
            "stale versus degraded projection regression coverage",
            "unreachable versus stopped state regression coverage",
            "cached-truth honesty checks",
        ]
    if retrieval_ranking_focus:
        return [
            "freshness precedence regression coverage",
            "applicability-boundary ranking tests",
            "missing-evidence disclosure regression coverage",
        ]
    if truth_contract_focus:
        return [
            "source-of-truth precedence regression coverage",
            "append-only assistant event log validation",
            "write-generation arbitration regression coverage",
        ]
    if _task_archetype(task_profile) == "implementation_change":
        return [
            "targeted regression coverage for the primary owner behavior",
            "integration or smoke validation for the touched execution path",
        ]
    return []


def _stage7_acceptance_criteria(
    *,
    job_contract: Mapping[str, Any],
    owner_targets: Sequence[Mapping[str, Any]],
    validation_targets: Sequence[Mapping[str, Any]],
    truth_contract_focus: bool,
    orchestration_focus: bool,
    ui_projection_focus: bool,
    retrieval_ranking_focus: bool,
    scheduler_focus: bool,
) -> List[str]:
    criteria = _dedupe_strings(str(item) for item in list(job_contract.get("acceptance_criteria") or []))
    if truth_contract_focus:
        criteria.append("owner truth surfaces remain authoritative over projections, caches, and duplicated copies")
    if orchestration_focus:
        criteria.append("orchestration state transitions stay replay-safe across restart and crash windows")
    if ui_projection_focus:
        criteria.append("UI states distinguish live, stale, degraded, unreachable, and stopped conditions honestly")
    if retrieval_ranking_focus:
        criteria.append("direct repository evidence outranks generic planning hubs and stale memory during ranking")
    if scheduler_focus:
        criteria.append("scheduler recovery behavior remains bounded, restart-safe, and resistant to stale claims")
    if owner_targets and validation_targets:
        criteria.append("owner and validation surfaces stay aligned so tests reflect the intended implementation contract")
    return _dedupe_strings(criteria)


def _deterministic_patch_plan(bundle_obj: Mapping[str, Any], model_used: bool, warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    intake_assessment = dict(bundle_obj.get("intake_assessment") or {})
    job_contract = dict(bundle_obj.get("job_contract") or {})
    task_profile = dict(bundle_obj.get("task_profile") or {})
    families = _effective_families(job_contract, task_profile)
    truth_contract_focus = _is_truth_contract_family(families, task_profile)
    orchestration_focus = _is_orchestration_checkpoint_family(families, task_profile)
    ui_projection_focus = _is_ui_truth_projection_family(families, task_profile)
    retrieval_ranking_focus = _is_retrieval_ranking_family(families, task_profile)
    context_bundle = dict(bundle_obj.get("context_bundle") or {})
    ppm = dict(context_bundle.get("planning_prompt_material") or {})
    replace_spec = dict(ppm.get("replace_spec") or {})
    is_global_replace = str(ppm.get("selection_mode") or "") == "exhaustive_literal_replace" and bool(replace_spec)
    files = list(context_bundle.get("files") or [])
    editable_files = [dict(entry) for entry in list(context_bundle.get("editable_targets") or [])]
    if not editable_files:
        editable_files = [
            dict(entry)
            for entry in files
            if str(entry.get("role") or "") in EDITABLE_ROLES and bool(entry.get("edit_allowed"))
        ]
    if not editable_files:
        repo_root = Path(str(job_contract.get("repo_root") or ".")).resolve()
        fallback_allowed_paths = _dedupe_strings(job_contract.get("allowed_paths") or [])
        for allowed_path in fallback_allowed_paths:
            try:
                rel_path = normalize_repo_relative_path(repo_root, allowed_path)
            except Exception:
                rel_path = str(allowed_path or "").replace("\\", "/").strip("/")
            if not rel_path:
                continue
            editable_files.append(
                {
                    "path": rel_path,
                    "role": "primary_target",
                    "bundle_role": "primary_target",
                    "file_role": "implementation_code",
                    "candidate_role": "implementation_code",
                    "ownership_role": "core_owner",
                    "selection_lane": "implementation_target",
                    "edit_allowed": True,
                    "selection_score": 1.0,
                    "targetability_score": 1.0,
                    "selection_reasons": ["fallback_allowed_path"],
                    "responsibility": ["execution_contract"],
                    "subsystems": ["execution"],
                    "snippets": [],
                }
            )
    read_only_context_entries = [dict(entry) for entry in list(context_bundle.get("read_only_context") or [])]
    if not read_only_context_entries:
        read_only_context_entries = [dict(entry) for entry in files if not (str(entry.get("role") or "") in EDITABLE_ROLES and bool(entry.get("edit_allowed")))]
    read_only_paths = [str(entry.get("path") or "") for entry in read_only_context_entries]

    modify_files: List[Dict[str, Any]] = []
    execution_targets: List[Dict[str, Any]] = []
    implementation_steps: List[str] = []
    risks: List[str] = []
    ambiguity_flags: List[str] = []
    required_tests = [str(x) for x in job_contract.get("issue_context", {}).get("failing_tests", [])]

    for file_entry in editable_files:
        path = str(file_entry.get("path") or "")
        if _forbidden_modify_path(path, families=families, task_profile=task_profile, is_global_replace=is_global_replace) and not _is_docs_task(families, task_profile):
            continue
        snippets = list(file_entry.get("snippets") or [])
        symbols = sorted({str(snippet.get("symbol") or "").strip() for snippet in snippets if str(snippet.get("symbol") or "").strip()})
        snippet_ids = [str(snippet.get("snippet_id") or "") for snippet in snippets if str(snippet.get("snippet_id") or "").strip()]
        allowed_edit_regions = [dict(snippet.get("allowed_edit_region") or {}) for snippet in snippets if isinstance(snippet.get("allowed_edit_region"), Mapping)]
        intended = _default_edit_intent(
            path,
            candidate_role=str(file_entry.get("candidate_role") or "other"),
            families=families,
            replace_spec=replace_spec,
            is_global_replace=is_global_replace,
        )
        change_type = _change_type_for_entry(file_entry)
        edit_intent = intended[0] if intended else f"update {Path(path).name}"
        target_payload = {
            "path": path,
            "role": str(file_entry.get("role") or ""),
            "candidate_role": str(file_entry.get("candidate_role") or "other"),
            "ownership_role": str(file_entry.get("ownership_role") or ""),
            "change_type": change_type,
            "edit_intent": edit_intent,
            "symbols": symbols,
            "snippet_ids": snippet_ids,
            "allowed_edit_regions": allowed_edit_regions,
            "rationale": list(file_entry.get("selection_reasons") or ["selected by deterministic planning"]),
        }
        execution_targets.append(target_payload)
        modify_files.append({
            **target_payload,
            "symbols_to_edit": symbols,
            "intended_modifications": intended,
            "acceptance_criteria_coverage": [
                {"criterion": str(x), "coverage": "indirect" if idx else "direct"}
                for idx, x in enumerate(job_contract.get("acceptance_criteria", [])[:3])
            ],
            "risk_level": "medium" if file_entry.get("role") in {"primary_target", "config_target", "paired_test"} else "low",
        })

    scheduler_focus = _is_scheduler_family(families)
    focused_outcome_family = bool(truth_contract_focus or orchestration_focus or ui_projection_focus or retrieval_ranking_focus or scheduler_focus)
    failure_taxonomy = _coerce_mapping(intake_assessment.get("failure_taxonomy"))
    dominant_owner_targets = [
        dict(entry)
        for entry in execution_targets
        if _is_owner_modify_candidate(entry)
    ]
    secondary_impl_targets = [
        dict(entry)
        for entry in execution_targets
        if str(entry.get("candidate_role") or "") in {"implementation_code", "config_file"}
        and str(entry.get("ownership_role") or "") != "core_owner"
    ]
    paired_test_targets = [
        dict(entry)
        for entry in execution_targets
        if _is_test_modify_candidate(entry)
    ]
    explicit_test_edit_request = _objective_requests_test_edits(job_contract)
    if explicit_test_edit_request:
        existing_modify_paths = {str(entry.get("path") or "") for entry in modify_files}
        promotable_test_entries = [
            dict(entry)
            for entry in list(read_only_context_entries)
            if _is_test_modify_candidate(entry) and str(entry.get("path") or "") not in existing_modify_paths
        ]
        for entry in promotable_test_entries:
            promoted = _promote_test_modify_target(entry)
            modify_files.append(promoted)
            paired_test_targets.append(dict(promoted))
            execution_targets.append(dict(promoted))
            existing_modify_paths.add(str(promoted.get("path") or ""))
        if promotable_test_entries:
            promoted_paths = {str(entry.get("path") or "") for entry in promotable_test_entries}
            read_only_context_entries = [
                entry for entry in read_only_context_entries if str(entry.get("path") or "") not in promoted_paths
            ]
            read_only_paths = [path for path in read_only_paths if path not in promoted_paths]
            ambiguity_flags = [
                flag
                for flag in ambiguity_flags
                if flag != "Some high-signal files were retained as read-only context rather than edit targets"
            ]
    approved_modify_paths = {
        str(entry.get("path") or "")
        for entry in modify_files
        if str(entry.get("path") or "").strip()
    }
    if approved_modify_paths:
        read_only_context_entries = [
            entry for entry in read_only_context_entries if str(entry.get("path") or "") not in approved_modify_paths
        ]
        read_only_paths = [path for path in read_only_paths if path not in approved_modify_paths]

    local_contract_refs = [
        dict(entry)
        for entry in read_only_context_entries
        if str(entry.get("ownership_role") or "") == "local_contract_doc"
    ]
    non_model_warnings = [
        str(item)
        for item in (warnings or [])
        if "model planning disabled" not in str(item).lower()
    ]

    if modify_files:
        implementation_steps.append("Review editable targets and confirm allowed edit regions before patch generation")
        implementation_steps.extend([f"Modify {Path(item['path']).name} according to execution contract" for item in modify_files[:5]])
    if read_only_paths:
        implementation_steps.append("Use read-only context files for reference only; do not mutate unless scope is explicitly expanded")
    if any(_is_bundle_reference_only_path(p) for p in read_only_paths):
        risks.append("Read-only context includes docs/governance/runtime artifacts that explain the task but should not be patched")
    if _is_scheduler_family(families):
        risks.append("Robustness changes often cross watchdog, recovery, scheduling, and operator-flow boundaries")
        if not required_tests:
            required_tests.extend(_default_required_tests_for_contract(
                families=families,
                task_profile=task_profile,
                truth_contract_focus=truth_contract_focus,
                orchestration_focus=orchestration_focus,
                ui_projection_focus=ui_projection_focus,
                retrieval_ranking_focus=retrieval_ranking_focus,
                scheduler_focus=True,
            ))

    if read_only_paths:
        ambiguity_flags.append("Some high-signal files were retained as read-only context rather than edit targets")

    dominant_owner_score = max((float(entry.get("targetability_score") or entry.get("selection_score") or 0.0) for entry in editable_files if str(entry.get("ownership_role") or "") == "core_owner"), default=0.0)
    second_owner_score = max((float(entry.get("targetability_score") or entry.get("selection_score") or 0.0) for entry in editable_files if str(entry.get("ownership_role") or "") == "core_owner" and float(entry.get("targetability_score") or entry.get("selection_score") or 0.0) < dominant_owner_score), default=-999.0)
    owner_dominant = bool(len(dominant_owner_targets) == 1 and (dominant_owner_score - second_owner_score >= (1.2 if focused_outcome_family else 1.5) or second_owner_score < 0))
    retrieval_owner_cluster = [
        entry for entry in editable_files
        if str(entry.get("ownership_role") or "") == "core_owner"
        and str(entry.get("path") or "").startswith("infra/pipeline/")
    ]
    retrieval_cluster_ready = bool(
        retrieval_ranking_focus
        and 1 <= len(retrieval_owner_cluster) <= 2
        and all(str(entry.get("path") or "").startswith("infra/pipeline/") for entry in retrieval_owner_cluster)
    )
    actionable_non_model_warnings = [
        warning for warning in non_model_warnings
        if not (
            retrieval_cluster_ready
            and ("retained as read-only context" in str(warning).lower() or "truncation" in str(warning).lower())
        )
    ]
    focused_outcome_ready = owner_dominant or retrieval_cluster_ready
    task_archetype = _task_archetype(task_profile)
    deterministic_bundle_ready = bool(
        focused_outcome_family
        and context_bundle.get("bundle_ready_for_execution")
        and dominant_owner_targets
        and required_tests
        and float(context_bundle.get("bundle_confidence") or 0.0) >= 0.72
        and float(task_profile.get("ambiguity_score") or 0.0) < 0.62
        and not actionable_non_model_warnings
    )
    test_first_task = _is_test_first_task(families, task_profile)
    multi_surface_expected = bool(
        truth_contract_focus
        or orchestration_focus
        or ui_projection_focus
        or retrieval_ranking_focus
        or scheduler_focus
    )
    acceptance_criteria = _stage7_acceptance_criteria(
        job_contract=job_contract,
        owner_targets=dominant_owner_targets + secondary_impl_targets,
        validation_targets=paired_test_targets,
        truth_contract_focus=truth_contract_focus,
        orchestration_focus=orchestration_focus,
        ui_projection_focus=ui_projection_focus,
        retrieval_ranking_focus=retrieval_ranking_focus,
        scheduler_focus=scheduler_focus,
    )
    implementation_target_files = _dedupe_strings(
        [entry.get("path") for entry in dominant_owner_targets]
        + [entry.get("path") for entry in secondary_impl_targets]
        + [
            entry.get("path")
            for entry in modify_files
            if not _is_test_modify_candidate(entry)
        ]
    )
    validation_target_files = _dedupe_strings(
        [entry.get("path") for entry in paired_test_targets]
        + [
            entry.get("path")
            for entry in read_only_context_entries
            if _is_test_modify_candidate(entry)
        ]
    )
    if dominant_owner_targets and not required_tests:
        required_tests = _default_required_tests_for_contract(
            families=families,
            task_profile=task_profile,
            truth_contract_focus=truth_contract_focus,
            orchestration_focus=orchestration_focus,
            ui_projection_focus=ui_projection_focus,
            retrieval_ranking_focus=retrieval_ranking_focus,
            scheduler_focus=scheduler_focus,
        )
    validation_only_candidate = bool(
        focused_outcome_ready
        and focused_outcome_family
        and paired_test_targets
        and not actionable_non_model_warnings
        and float(task_profile.get("ambiguity_score") or 0.0) < 0.6
        and (not truth_contract_focus or local_contract_refs)
        and (
            (test_first_task and not dominant_owner_targets)
            or ((truth_contract_focus or retrieval_ranking_focus) and bool(dominant_owner_targets))
        )
    )
    tests_required_candidate = bool(
        focused_outcome_ready
        and focused_outcome_family
        and dominant_owner_targets
        and not paired_test_targets
        and float(task_profile.get("ambiguity_score") or 0.0) < (0.72 if retrieval_ranking_focus else 0.7)
    )
    already_compliant_candidate = bool(validation_only_candidate and truth_contract_focus and not required_tests and not modify_files)

    for item in modify_files:
        item["acceptance_criteria_coverage"] = [
            {"criterion": str(x), "coverage": "direct" if idx < 2 else "indirect"}
            for idx, x in enumerate(acceptance_criteria[:4])
        ]

    if tests_required_candidate:
        if not required_tests:
            required_tests = _default_required_tests_for_contract(
                families=families,
                task_profile=task_profile,
                truth_contract_focus=truth_contract_focus,
                orchestration_focus=orchestration_focus,
                ui_projection_focus=ui_projection_focus,
                retrieval_ranking_focus=retrieval_ranking_focus,
                scheduler_focus=scheduler_focus,
            )
        required_tests = _dedupe_strings(required_tests)
        risks.append("Execution contract lacks an explicit validation target file; downstream work must add or update focused regression coverage")
        unresolved_validation_gap = "Owner mutation is in scope but the contract lacks a paired validation target file"
        if unresolved_validation_gap not in ambiguity_flags:
            ambiguity_flags.append(unresolved_validation_gap)
        modify_files = []
        implementation_steps = [
            "Modify the primary owner surfaces first and keep the scope bounded to the retrieved implementation contract",
            "Add or update focused regression coverage that exercises the owner surface and the relevant failure windows",
            "Do not treat the patch as execution-ready until owner edits and validation coverage are both represented",
        ]

    if validation_only_candidate:
        validation_tests = required_tests or _default_required_tests_for_contract(
            families=families,
            task_profile=task_profile,
            truth_contract_focus=truth_contract_focus,
            orchestration_focus=orchestration_focus,
            ui_projection_focus=ui_projection_focus,
            retrieval_ranking_focus=retrieval_ranking_focus,
            scheduler_focus=scheduler_focus,
        ) or [
            "targeted validation coverage for the dominant owner surface",
            "regression checks for the contract-specific failure modes",
        ]
        required_tests = _dedupe_strings(validation_tests)
        if paired_test_targets:
            execution_targets = paired_test_targets
            modify_files = [item for item in modify_files if str(item.get("candidate_role") or "") == "test_file"]
        else:
            execution_targets = []
            modify_files = []
        if truth_contract_focus:
            implementation_steps = [
                "Preserve the dominant truth-contract owner unless validation uncovers a concrete contract gap",
                "Use local contract and sibling boundary context to validate precedence, arbitration, and append-only evidence rules",
                "Add or tighten paired validation coverage before proposing owner-code edits",
            ]
        elif orchestration_focus:
            implementation_steps = [
                "Preserve the dominant orchestration owner unless crash-window validation exposes a concrete checkpoint gap",
                "Use paired orchestration tests to validate started-before-side-effect and completed-before-advance semantics",
                "Only escalate to code edits if replay-safety or restart-honesty checks fail",
            ]
        elif ui_projection_focus:
            implementation_steps = [
                "Preserve the projection owner unless stale or degraded-state validation exposes a concrete honesty gap",
                "Use paired projection tests to validate stale, degraded, reconnecting, and unreachable distinctions",
                "Only escalate to code edits if cached truth is presented as live truth or backend-role uncertainty is hidden",
            ]
        elif retrieval_ranking_focus:
            implementation_steps = [
                "Preserve the dominant retrieval-ranking owner unless validation exposes a concrete freshness or applicability gap",
                "Use paired pipeline tests to validate evidence-first ordering, freshness precedence, and missing-evidence disclosure",
                "Only escalate to code edits if stale memory or unrelated project evidence can outrank direct evidence",
            ]
        elif scheduler_focus:
            implementation_steps = [
                "Preserve the dominant scheduler owner unless validation exposes a concrete robustness gap",
                "Use paired scheduler tests to validate stale-claim cleanup, watchdog classification, and startup recovery behavior",
                "Only escalate to code edits if the scheduler still misclassifies blocked states or allows stale claims to survive restart",
            ]
        else:
            implementation_steps = [
                "Preserve the dominant owner unless validation uncovers a concrete implementation gap",
                "Use paired tests and read-only context to validate the targeted contract edges before proposing owner-code edits",
                "Only escalate to code modification if validation exposes a concrete failing behavior or missing invariant enforcement",
            ]
        ambiguity_flags = [flag for flag in ambiguity_flags if flag != "Some high-signal files were retained as read-only context rather than edit targets"]

    if multi_surface_expected and dominant_owner_targets:
        owner_paths = {str(entry.get("path") or "") for entry in dominant_owner_targets}
        validation_paths = {str(entry.get("path") or "") for entry in paired_test_targets}
        existing_paths = {str(entry.get("path") or "") for entry in modify_files}
        should_add_validation_target = bool(paired_test_targets and owner_paths and not validation_only_candidate)
        if should_add_validation_target:
            for item in execution_targets:
                path = str(item.get("path") or "")
                if path not in existing_paths and path in validation_paths:
                    modify_files.append(
                        {
                            **item,
                            "symbols_to_edit": list(item.get("symbols") or []),
                            "intended_modifications": ["add or adjust regression coverage that matches the owner-side contract change"],
                            "acceptance_criteria_coverage": [
                                {"criterion": str(x), "coverage": "direct" if "tests reflect" in str(x).lower() else "indirect"}
                                for x in acceptance_criteria[:4]
                            ],
                            "risk_level": "low",
                        }
                    )
                    existing_paths.add(path)
        if len(owner_paths) == 1 and not paired_test_targets and "Execution contract lacks an explicit validation target file; downstream work must add or update focused regression coverage" not in risks:
            risks.append("Execution contract has owner scope but only indirect validation context; tests must be reviewed before execution")

    unresolved = list((actionable_non_model_warnings or []) if (validation_only_candidate or tests_required_candidate) else (warnings or ([] if (modify_files or validation_only_candidate or tests_required_candidate) else ["No editable implementation targets survived planning filters"])))
    unresolved.extend(flag for flag in ambiguity_flags if flag not in unresolved)

    execution_status = _execution_readiness_status(
        editable_files=modify_files,
        task_profile=task_profile,
        families=families,
        warnings=actionable_non_model_warnings,
    )
    bounded_allowed_path_ready = bool(
        modify_files
        and _dedupe_strings(job_contract.get("allowed_paths") or [])
        and set(_dedupe_strings(entry.get("path") for entry in modify_files)).issubset(set(_dedupe_strings(job_contract.get("allowed_paths") or [])))
        and not actionable_non_model_warnings
    )
    should_escalate = execution_status != "READY_FOR_EXECUTION"
    plan_status = "READY_FOR_REVIEW" if not should_escalate else "INSUFFICIENT_CONTEXT"
    if bounded_allowed_path_ready:
        should_escalate = False
        execution_status = "READY_FOR_EXECUTION"
        plan_status = "READY_FOR_REVIEW"
    if tests_required_candidate:
        should_escalate = True
        execution_status = "READY_FOR_REVIEW"
        plan_status = "TESTS_REQUIRED"
    elif validation_only_candidate:
        should_escalate = False
        execution_status = "READY_FOR_EXECUTION" if modify_files else "READY_FOR_REVIEW"
        plan_status = "ALREADY_COMPLIANT" if already_compliant_candidate else "VALIDATION_ONLY"
    elif deterministic_bundle_ready:
        should_escalate = False
        execution_status = "READY_FOR_EXECUTION"
        plan_status = "READY_FOR_REVIEW"
    tiny_repo_execution_ready = bool(
        len(list(context_bundle.get("files") or [])) <= 2
        and len(modify_files) <= 2
        and modify_files
        and float(task_profile.get("ambiguity_score") or 0.0) < 0.72
        and not actionable_non_model_warnings
        and not validation_only_candidate
        and not tests_required_candidate
        and (
            task_archetype in {"implementation_change", "verification_test"}
            or explicit_test_edit_request
        )
    )
    if tiny_repo_execution_ready:
        should_escalate = False
        execution_status = "READY_FOR_EXECUTION"
        plan_status = "READY_FOR_REVIEW"

    patch_execution_contract = {
        "objective": str(job_contract.get("objective") or job_contract.get("description") or job_contract.get("title") or ""),
        "implementation_targets": execution_targets,
        "read_only_context": [
            {
                "path": str(entry.get("path") or ""),
                "role": str(entry.get("role") or ""),
                "candidate_role": str(entry.get("candidate_role") or ""),
                "selection_lane": str(entry.get("selection_lane") or "reference_context"),
            }
            for entry in read_only_context_entries
        ],
        "constraints": [str(x) for x in job_contract.get("constraints", [])],
        "forbidden_targets": ["docs/", "runtime/", "tmp/", "governance/", "reports/", "*.md", "*.txt", "*.rst"],
        "required_output": {
            "type": "execution_contract_v1",
            "must_include": ["modify_files", "required_tests", "execution_readiness"],
        },
        "acceptance_criteria": acceptance_criteria,
        "required_tests": required_tests,
        "ambiguity_flags": ambiguity_flags,
        "execution_readiness": {
            "status": execution_status,
            "bundle_ready_for_execution": bool(context_bundle.get("bundle_ready_for_execution")),
            "bundle_confidence": context_bundle.get("bundle_confidence"),
            "weakness_reasons": list(context_bundle.get("bundle_weakness_reasons") or []),
        },
        "stage8_payload": {
            "objective": str(job_contract.get("objective") or job_contract.get("description") or job_contract.get("title") or ""),
            "targets": execution_targets,
            "read_only_context": [str(entry.get("path") or "") for entry in read_only_context_entries],
            "constraints": [str(x) for x in job_contract.get("constraints", [])],
            "output_format": "json_execution_result",
        },
    }

    output_contract_mode = str(task_profile.get("output_contract_mode") or "patch_contract")
    design_execution_contract: Dict[str, Any] = {}
    assessment_report_contract: Dict[str, Any] = {}
    retrieval_answer_contract: Dict[str, Any] = {}

    if task_archetype == "design_new_surface":
        should_escalate = False if (read_only_context_entries or execution_targets) else should_escalate
        execution_status = "READY_FOR_REVIEW" if not execution_targets else execution_status
        plan_status = "DESIGN_READY_FOR_REVIEW" if not should_escalate else plan_status
        if not modify_files:
            implementation_steps = [
                "Identify the new artifact boundaries and preserve the existing support context as read-only design input",
                "Use implementation patterns, contracts, and integration anchors to define the new surface coherently",
                "Only promote existing files to modify targets when a concrete integration edit is necessary",
            ]
        design_execution_contract = {
            "new_artifacts_expected": [str(job_contract.get("title") or job_contract.get("objective") or "new_surface")],
            "supporting_reference_files": [str(entry.get("path") or "") for entry in read_only_context_entries],
            "integration_points": [str(entry.get("path") or "") for entry in execution_targets],
            "constraints": [str(x) for x in job_contract.get("constraints", [])],
            "validation_expectations": required_tests,
            "open_design_questions": unresolved,
        }
    elif task_archetype == "repo_assessment":
        modify_files = []
        should_escalate = False
        execution_status = "READY_FOR_REVIEW"
        plan_status = "ASSESSMENT_READY"
        implementation_steps = [
            "Review representative code, test, config, and documentation surfaces rather than forcing an edit target",
            "Summarize systemic strengths, fragility points, and missing safety rails with direct evidence",
            "Recommend follow-up audits or remediation themes instead of proposing repository mutation by default",
        ]
        assessment_report_contract = {
            "findings": risks or ["Assessment bundle assembled; inspect evidence_files for repo health themes"],
            "evidence_files": [str(entry.get("path") or "") for entry in read_only_context_entries],
            "risk_areas": unresolved,
            "confidence": context_bundle.get("bundle_confidence"),
            "missing_evidence": list(context_bundle.get("bundle_weakness_reasons") or []),
            "recommended_next_checks": required_tests or ["target one subsystem for deeper reliability audit", "review representative tests and config ownership"],
        }
    elif task_archetype in {"information_retrieval", "documentation_contract"}:
        modify_files = []
        should_escalate = False
        execution_status = "READY_FOR_REVIEW"
        plan_status = "ANSWER_READY"
        implementation_steps = [
            "Assemble the strongest supporting files and snippets for the requested lookup scope",
            "Preserve all selected files as read-only evidence unless the user explicitly asks for mutation",
            "Surface ambiguity or missing evidence instead of fabricating an edit target",
        ]
        retrieval_answer_contract = {
            "answer_scope": str(job_contract.get("objective") or job_contract.get("description") or job_contract.get("title") or ""),
            "supporting_files": [str(entry.get("path") or "") for entry in read_only_context_entries],
            "evidence_bundle_roles": [str(entry.get("role") or entry.get("bundle_role") or "") for entry in read_only_context_entries],
            "ambiguities": unresolved,
        }

    plan = {
        "objective": str(job_contract.get("objective") or job_contract.get("description") or job_contract.get("title") or ""),
        "modify_files": modify_files,
        "files_read_only_context": read_only_paths,
        "implementation_steps": implementation_steps,
        "risks": risks,
        "non_goals": ["no code patch emitted in planning stage", "no repository mutation"],
        "assumptions": _dedupe_strings(
            [
                "retrieved context is sufficient to name likely owner and validation surfaces",
                "read-only context files remain reference boundaries unless the operator expands scope",
            ]
            + (
                ["paired validation coverage may still need to be added or expanded before execution"]
                if tests_required_candidate
                else []
            )
        ),
        "acceptance_criteria": acceptance_criteria,
        "unresolved_uncertainties": unresolved,
        "required_tests": required_tests,
        "implementation_target_files": implementation_target_files,
        "validation_target_files": validation_target_files,
        "planning_confidence": 0.9 if (is_global_replace and modify_files) else (0.78 if tests_required_candidate else (0.84 if validation_only_candidate else (0.82 if execution_status == "READY_FOR_EXECUTION" else (0.58 if modify_files else 0.52 if task_archetype in {"design_new_surface", "repo_assessment", "information_retrieval", "documentation_contract"} else 0.28)))),
        "should_escalate": should_escalate,
        "plan_status": plan_status,
        "plan_outcome": plan_status,
        "execution_status": execution_status,
        "outcome_type": (
            "validation_only" if validation_only_candidate else
            "tests_required" if tests_required_candidate else
            "answer_only" if task_archetype in {"information_retrieval", "documentation_contract"} else
            "assessment_only" if task_archetype == "repo_assessment" else
            "design_contract" if task_archetype == "design_new_surface" and not modify_files else
            "mutation_ready" if execution_status == "READY_FOR_EXECUTION" and modify_files else
            "review_only"
        ),
        "model_used": model_used,
        "planning_mode": "global_replace" if is_global_replace else "targeted_change",
        "output_contract_mode": output_contract_mode,
        "failure_mappings": {
            key: [str(item) for item in list(failure_taxonomy.get(key) or [])]
            for key in ["operator_failure_modes", "system_failure_modes", "concurrency_failure_modes", "recovery_failure_modes"]
        },
        "acceptance_contract": {
            "mutation_planned": bool(modify_files),
            "required_tests_present": bool(required_tests),
            "review_only": bool(not modify_files and task_archetype in {"design_new_surface", "repo_assessment", "information_retrieval", "documentation_contract"}),
            "validation_only": bool(validation_only_candidate),
            "explicit_test_edit_request": bool(explicit_test_edit_request),
        },
        "forbidden_targets": patch_execution_contract["forbidden_targets"],
        "must_not_change": _dedupe_strings(
            path
            for path in (read_only_paths + patch_execution_contract["forbidden_targets"])
            if path not in approved_modify_paths
        ),
        "change_boundaries": _dedupe_strings(entry.get("path") for entry in modify_files),
        "quality_metrics": {
            "modify_file_count": len(modify_files),
            "owner_target_count": len(dominant_owner_targets) + len(secondary_impl_targets),
            "validation_target_count": len([entry for entry in modify_files if _is_test_modify_candidate(entry)]),
            "required_test_count": len(required_tests),
            "multi_surface_expected": multi_surface_expected,
        },
        "patch_execution_contract": patch_execution_contract,
        "design_execution_contract": design_execution_contract,
        "assessment_report_contract": assessment_report_contract,
        "retrieval_answer_contract": retrieval_answer_contract,
    }
    if is_global_replace:
        plan["replace_spec"] = replace_spec
    return ensure_patch_plan_foundation(plan, intake_assessment=intake_assessment)



def maybe_model_patch_plan(bundle_obj: Mapping[str, Any], config: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    warnings: List[str] = []
    intake_assessment = dict(bundle_obj.get("intake_assessment") or {})
    planning = dict(config.get("planning") or {})
    stage_cfg = dict(config.get("_stage") or {})
    model_enabled = bool(planning.get("enable_model", False))
    allow_remote = bool(stage_cfg.get("allow_remote_model", False))
    if not model_enabled:
        warnings.append("model planning disabled by config; deterministic fallback used")
        return _deterministic_patch_plan(bundle_obj, False, warnings), warnings
    if not allow_remote:
        warnings.append("remote model not allowed for patch planning; deterministic fallback used")
        return _deterministic_patch_plan(bundle_obj, False, warnings), warnings
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        warnings.append("OPENAI_API_KEY missing; deterministic fallback used")
        return _deterministic_patch_plan(bundle_obj, False, warnings), warnings
    try:
        from openai import OpenAI  # type: ignore
    except Exception:
        warnings.append("openai package unavailable; deterministic fallback used")
        return _deterministic_patch_plan(bundle_obj, False, warnings), warnings
    model_name = str(planning.get("model_name") or "gpt-5.4-mini")
    prompt = build_patch_plan_prompt(bundle_obj)
    try:
        client = OpenAI(api_key=api_key)
        response = client.responses.create(model=model_name, input=prompt)
        text = getattr(response, "output_text", "") or ""
        payload = json.loads(text)
        patch_plan = dict(payload.get("patch_plan") or payload)
        if not isinstance(patch_plan.get("modify_files"), list) or not str(patch_plan.get("objective") or "").strip():
            raise ValueError("patch plan missing required fields")
        patch_plan.setdefault("model_used", True)
        patch_plan.setdefault("plan_status", "READY_FOR_REVIEW")
        patch_plan = _sanitize_patch_plan_against_bundle(bundle_obj, patch_plan, warnings)
        patch_plan = ensure_patch_plan_foundation(patch_plan, intake_assessment=intake_assessment)
        return patch_plan, warnings
    except Exception as exc:
        warnings.append(f"model output invalid; deterministic fallback used: {exc}")
        return _deterministic_patch_plan(bundle_obj, False, warnings), warnings


def _execution_contract_budget_config(config: Mapping[str, Any]) -> Dict[str, int]:
    limits = _coerce_mapping(config.get("limits"))
    execution_budget = _coerce_mapping(config.get("execution_contract_budget"))
    budgets = dict(DEFAULT_EXECUTION_CONTRACT_BUDGETS)
    alias_map = {
        "execution_contract_max_files": "max_files",
        "execution_contract_max_full_files": "max_full_files",
        "execution_contract_max_snippets": "max_snippets",
        "execution_contract_total_chars": "total_chars",
        "execution_contract_full_file_chars": "full_file_chars",
    }
    for source_key, target_key in alias_map.items():
        value = limits.get(source_key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            budgets[target_key] = int(value)
    for key, value in execution_budget.items():
        if key in budgets and isinstance(value, (int, float)) and not isinstance(value, bool):
            budgets[key] = int(value)
    return budgets


def _estimate_tokens_from_chars(char_count: int) -> int:
    return max(1, int(round(max(0, char_count) / 4.0))) if char_count else 0


def build_execution_contract(
    bundle_obj: Mapping[str, Any],
    patch_plan: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    source_patch_plan_artifact: str = "07_patch_plan.json",
    source_context_bundle_artifact: str = "06_context_bundle.json",
) -> Dict[str, Any]:
    job_contract = _coerce_mapping(bundle_obj.get("job_contract"))
    task_profile = _coerce_mapping(bundle_obj.get("task_profile"))
    context_bundle = ensure_context_bundle_foundation(bundle_obj.get("context_bundle"))
    repo_root = Path(str(job_contract.get("repo_root") or "")).resolve()
    task_archetype = _task_archetype(task_profile)
    output_contract_mode = str(patch_plan.get("output_contract_mode") or task_profile.get("output_contract_mode") or "patch_contract")
    objective = str(patch_plan.get("objective") or job_contract.get("objective") or job_contract.get("description") or job_contract.get("title") or "")
    modify_files = [dict(entry) for entry in list(patch_plan.get("modify_files") or []) if isinstance(entry, Mapping)]
    fallback_selection_used = False
    if not modify_files and task_archetype == "implementation_change" and output_contract_mode == "patch_contract":
        fallback_candidates = [dict(entry) for entry in list(context_bundle.get("editable_targets") or []) if isinstance(entry, Mapping)]
        if fallback_candidates:
            fallback_candidates.sort(
                key=lambda entry: (
                    0 if str(entry.get("ownership_role") or "") == "core_owner" else 1,
                    0 if str(entry.get("bundle_role") or entry.get("role") or "") in {"primary_target", "config_target", "edit_target"} else 1,
                    -float(entry.get("selection_score") or entry.get("targetability_score") or 0.0),
                    str(entry.get("path") or ""),
                )
            )
            fallback = fallback_candidates[0]
            modify_files = [{
                "path": str(fallback.get("path") or ""),
                "role": str(fallback.get("bundle_role") or fallback.get("role") or ""),
                "candidate_role": str(fallback.get("candidate_role") or ""),
                "ownership_role": str(fallback.get("ownership_role") or ""),
                "change_type": _change_type_for_entry(fallback),
                "edit_intent": "recover edit scope from bounded context bundle",
            }]
            fallback_selection_used = True
    modify_paths = _dedupe_strings(entry.get("path") for entry in modify_files)
    modify_path_set = {path for path in modify_paths if path}
    patch_plan_status = str(patch_plan.get("plan_status") or patch_plan.get("plan_outcome") or "")
    patch_execution_status = str(patch_plan.get("execution_status") or "")
    read_only_context_paths = _dedupe_strings(
        path
        for path in (
            list(patch_plan.get("files_read_only_context") or [])
            + [
                entry.get("path")
                for entry in list(context_bundle.get("read_only_context") or [])
                if isinstance(entry, Mapping)
            ]
        )
        if str(path or "") and str(path or "") not in modify_path_set
    )
    forbidden_targets = _dedupe_strings(
        path
        for path in (
            list(job_contract.get("forbidden_paths") or [])
            + list(patch_plan.get("forbidden_targets") or [])
            + read_only_context_paths
        )
        if str(path or "") and str(path or "") not in modify_path_set
    )
    must_not_change = _dedupe_strings(
        path
        for path in (list(patch_plan.get("must_not_change") or []) + read_only_context_paths)
        if str(path or "") and str(path or "") not in modify_path_set
    )
    expected_new_files = []
    design_contract = _coerce_mapping(patch_plan.get("design_execution_contract"))
    assessment_contract = _coerce_mapping(patch_plan.get("assessment_report_contract"))
    retrieval_contract = _coerce_mapping(patch_plan.get("retrieval_answer_contract"))
    if task_archetype == "design_new_surface":
        expected_new_files = _dedupe_strings(design_contract.get("new_artifacts_expected") or [])

    execution_mode = "patch_generation"
    generation_applicable = bool(modify_paths)
    generation_blocked_reason: Optional[str] = None
    if task_archetype == "design_new_surface" and not modify_paths:
        execution_mode = "design_contract_only"
        generation_applicable = False
        generation_blocked_reason = "design contract prepared but no concrete edit targets were approved"
    elif task_archetype == "repo_assessment":
        execution_mode = "repo_assessment"
        generation_applicable = False
        generation_blocked_reason = "repo assessment contract is review-only and does not authorize patch generation"
    elif task_archetype in {"information_retrieval", "documentation_contract"}:
        execution_mode = "retrieval_only"
        generation_applicable = False
        generation_blocked_reason = "retrieval or documentation contract does not authorize patch generation"
    elif patch_execution_status and patch_execution_status != "READY_FOR_EXECUTION":
        execution_mode = "review_only"
        generation_applicable = False
        generation_blocked_reason = f"patch plan readiness is {patch_execution_status.lower()} and requires review before generation"
    elif not modify_paths:
        execution_mode = "review_only"
        generation_applicable = False
        generation_blocked_reason = "patch plan did not approve any edit targets"

    may_create_files = bool(task_archetype == "design_new_surface" and expected_new_files)
    must_not_create_files = not may_create_files

    budgets = _execution_contract_budget_config(config)
    files = [dict(entry) for entry in list(context_bundle.get("files") or []) if isinstance(entry, Mapping)]
    file_by_path = {str(entry.get("path") or ""): entry for entry in files if str(entry.get("path") or "").strip()}
    ordered_paths = _dedupe_strings(modify_paths + [entry.get("path") for entry in files])

    contract_entries: List[Dict[str, Any]] = []
    generation_entries: List[Dict[str, Any]] = []
    degradation_actions: List[str] = []

    for path in ordered_paths:
        file_entry = file_by_path.get(path)
        if not file_entry:
            continue
        file_role = str(file_entry.get("file_role") or classify_file_role(path, ""))
        bundle_role = str(file_entry.get("bundle_role") or file_entry.get("role") or "reference_context")
        is_edit_target = path in modify_paths
        include_mode = "reference_only"
        reason_for_inclusion = str((list(file_entry.get("selection_reasons") or []) or [bundle_role or file_role or "selected context"])[0])
        if is_edit_target and generation_applicable:
            include_mode = "full_file"
            reason_for_inclusion = "approved edit target from patch plan"
        elif bundle_role in {"paired_test", "validation_target", "validation_context", "integration_context", "boundary_context", "new_file_support_context", "interface_contract_context"}:
            include_mode = "snippets_only"
            reason_for_inclusion = "supporting validation or integration context"
        elif file_role in {"test_code", "config_build", "schema_contract"}:
            include_mode = "snippets_only"
            reason_for_inclusion = "supporting test, config, or contract context"
        elif task_archetype == "design_new_surface" and path in _dedupe_strings(design_contract.get("integration_points") or []):
            include_mode = "snippets_only"
            reason_for_inclusion = "design integration anchor"
        contract_entries.append(
            {
                "path": path,
                "file_role": file_role,
                "bundle_role": bundle_role,
                "include_mode": include_mode,
                "reason_for_inclusion": reason_for_inclusion,
            }
        )

        abs_path = repo_root / path
        if include_mode == "full_file":
            if abs_path.exists() and abs_path.is_file():
                content = abs_path.read_text(encoding="utf-8", errors="replace")
                generation_entries.append(
                    {
                        "path": path,
                        "include_mode": include_mode,
                        "content": content,
                        "truncated": False,
                        "estimated_chars": len(content),
                        "priority": 0,
                        "is_edit_target": True,
                    }
                )
            else:
                contract_entries[-1]["include_mode"] = "snippets_only"
                contract_entries[-1]["reason_for_inclusion"] = "edit target missing on disk; fell back to snippet context"
                include_mode = "snippets_only"
        if include_mode == "snippets_only":
            snippets = []
            for snippet in list(file_entry.get("snippets") or [])[: max(1, budgets["max_snippets"])]:
                if not isinstance(snippet, Mapping):
                    continue
                snippets.append(
                    {
                        "start_line": int(snippet.get("start_line") or 1),
                        "end_line": int(snippet.get("end_line") or int(snippet.get("start_line") or 1)),
                        "symbol": str(snippet.get("symbol") or "") or None,
                        "snippet_role": str(snippet.get("snippet_role") or "reference_context"),
                        "content": str(snippet.get("content") or ""),
                    }
                )
            generation_entries.append(
                {
                    "path": path,
                    "include_mode": include_mode,
                    "snippets": snippets,
                    "truncated": False,
                    "estimated_chars": sum(len(str(item.get("content") or "")) for item in snippets),
                    "priority": 1 if bundle_role in {"paired_test", "validation_target", "validation_context"} else 2,
                    "is_edit_target": False,
                }
            )
        elif include_mode == "reference_only":
            generation_entries.append(
                {
                    "path": path,
                    "include_mode": include_mode,
                    "reference_reason": reason_for_inclusion,
                    "estimated_chars": 0,
                    "priority": 3,
                    "is_edit_target": False,
                }
            )

    def _apply_reference_demotions(entries: List[Dict[str, Any]]) -> None:
        mutable = sorted(
            [entry for entry in entries if not entry.get("is_edit_target") and entry.get("include_mode") == "snippets_only"],
            key=lambda item: (int(item.get("priority") or 99), str(item.get("path") or "")),
            reverse=True,
        )
        total_chars = sum(int(entry.get("estimated_chars") or 0) for entry in entries)
        while mutable and total_chars > budgets["total_chars"]:
            victim = mutable.pop(0)
            victim_path = str(victim.get("path") or "")
            victim["include_mode"] = "reference_only"
            victim["reference_reason"] = "demoted to reference_only due to prompt budget"
            victim["snippets"] = []
            degradation_actions.append(f"demoted_support_to_reference_only:{victim_path}")
            for contract_entry in contract_entries:
                if str(contract_entry.get("path") or "") == victim_path:
                    contract_entry["include_mode"] = "reference_only"
                    contract_entry["reason_for_inclusion"] = "support context demoted to reference_only due to prompt budget"
                    break
            total_chars = sum(int(entry.get("estimated_chars") or 0) for entry in entries)
            victim["estimated_chars"] = 0

    def _apply_full_file_caps(entries: List[Dict[str, Any]]) -> None:
        full_file_entries = [entry for entry in entries if entry.get("include_mode") == "full_file"]
        full_file_entries_sorted = sorted(full_file_entries, key=lambda item: (modify_paths.index(str(item.get("path") or "")) if str(item.get("path") or "") in modify_paths else 999, str(item.get("path") or "")))
        for overflow in full_file_entries_sorted[budgets["max_full_files"]:]:
            overflow_path = str(overflow.get("path") or "")
            source_file = file_by_path.get(overflow_path) or {}
            overflow["include_mode"] = "snippets_only"
            overflow["content"] = None
            overflow["truncated"] = False
            overflow["snippets"] = [
                {
                    "start_line": int(snippet.get("start_line") or 1),
                    "end_line": int(snippet.get("end_line") or int(snippet.get("start_line") or 1)),
                    "symbol": str(snippet.get("symbol") or "") or None,
                    "snippet_role": str(snippet.get("snippet_role") or "reference_context"),
                    "content": str(snippet.get("content") or ""),
                }
                for snippet in list(source_file.get("snippets") or [])[:2]
                if isinstance(snippet, Mapping)
            ]
            overflow["estimated_chars"] = sum(len(str(item.get("content") or "")) for item in list(overflow.get("snippets") or []))
            overflow["hydration_note"] = "full file hydration skipped because max_full_files budget was exceeded"
            degradation_actions.append(f"demoted_full_file_to_snippets:{overflow_path}")
            for contract_entry in contract_entries:
                if str(contract_entry.get("path") or "") == overflow_path:
                    contract_entry["include_mode"] = "snippets_only"
                    contract_entry["reason_for_inclusion"] = "edit target demoted to snippets_only because full-file budget was exceeded"
                    break
        for entry in [entry for entry in entries if entry.get("include_mode") == "full_file"]:
            if int(entry.get("estimated_chars") or 0) <= budgets["full_file_chars"]:
                continue
            overflow_path = str(entry.get("path") or "")
            source_file = file_by_path.get(overflow_path) or {}
            entry["include_mode"] = "snippets_only"
            entry["content"] = None
            entry["truncated"] = False
            entry["snippets"] = [
                {
                    "start_line": int(snippet.get("start_line") or 1),
                    "end_line": int(snippet.get("end_line") or int(snippet.get("start_line") or 1)),
                    "symbol": str(snippet.get("symbol") or "") or None,
                    "snippet_role": str(snippet.get("snippet_role") or "reference_context"),
                    "content": str(snippet.get("content") or ""),
                }
                for snippet in list(source_file.get("snippets") or [])[:2]
                if isinstance(snippet, Mapping)
            ]
            entry["estimated_chars"] = sum(len(str(item.get("content") or "")) for item in list(entry.get("snippets") or []))
            entry["hydration_note"] = "full file hydration skipped because file exceeded full_file_chars budget"
            degradation_actions.append(f"demoted_oversized_full_file:{overflow_path}")
            for contract_entry in contract_entries:
                if str(contract_entry.get("path") or "") == overflow_path:
                    contract_entry["include_mode"] = "snippets_only"
                    contract_entry["reason_for_inclusion"] = "edit target demoted to snippets_only because full-file size budget was exceeded"
                    break

    _apply_full_file_caps(generation_entries)
    _apply_reference_demotions(generation_entries)

    prompt_file_count = len(contract_entries)
    full_file_count = sum(1 for entry in generation_entries if entry.get("include_mode") == "full_file")
    snippet_file_count = sum(1 for entry in generation_entries if entry.get("include_mode") == "snippets_only")
    total_chars = sum(int(entry.get("estimated_chars") or 0) for entry in generation_entries)
    estimated_prompt_tokens = _estimate_tokens_from_chars(total_chars)
    prompt_budget = {
        "file_count": prompt_file_count,
        "full_file_count": full_file_count,
        "snippet_count": snippet_file_count,
        "reference_only_count": sum(1 for entry in contract_entries if entry.get("include_mode") == "reference_only"),
        "estimated_prompt_size_chars": total_chars,
        "estimated_prompt_tokens": estimated_prompt_tokens,
        "total_chars": total_chars,
        "max_total_chars": budgets["total_chars"],
        "degradation_policy": "demote support files to reference_only before demoting oversized full-file edit targets",
        "degradation_applied": bool(degradation_actions),
        "degradation_actions": degradation_actions,
    }

    stage8_output_contract = {
        "required_patch_artifact_type": "unified_diff",
        "allow_full_file_replacements": False,
        "allow_zip_artifacts": False,
        "return_prose": False,
    }
    stage9_validation_requirements = {
        "diff_must_parse": True,
        "paths_must_normalize_under_repo_root": True,
        "no_out_of_scope_paths": True,
        "no_absolute_paths": True,
        "no_path_traversal": True,
        "artifact_size_below_limit": True,
        "git_apply_check_required": True,
        "artifact_size_limit_chars": budgets["total_chars"],
    }
    stage10_apply_requirements = {
        "isolated_workspace_required": True,
        "source_repo_mutation_forbidden": True,
        "changed_files_must_be_enumerated": True,
        "apply_conflict_must_fail_closed": True,
    }

    required_tests = _resolve_required_test_commands(
        patch_plan_required_tests=list(patch_plan.get("required_tests") or []),
        validation_target_files=list(patch_plan.get("validation_target_files") or []),
        modify_files=modify_files,
    )
    stage11_static_verification = build_stage11_contract_section(config=config, allowed_edit_files=modify_paths)
    stage12_runtime_verification = build_stage12_contract_section(config=config, required_tests=required_tests)
    stage13_contract_verification = build_stage13_contract_section(
        static_contract=stage11_static_verification,
        runtime_contract=stage12_runtime_verification,
    )

    execution_contract = {
        "task_mode": task_archetype,
        "execution_mode": execution_mode,
        "output_contract_mode": output_contract_mode,
        "objective": objective,
        "generation_applicable": bool(generation_applicable),
        "generation_blocked_reason": generation_blocked_reason,
        "allowed_edit_files": modify_paths,
        "implementation_target_files": [str(item) for item in list(patch_plan.get("implementation_target_files") or [])],
        "validation_target_files": [str(item) for item in list(patch_plan.get("validation_target_files") or [])],
        "fallback_selection_used": bool(fallback_selection_used),
        "forbidden_edit_files": forbidden_targets,
        "must_not_create_files": bool(must_not_create_files),
        "may_create_files": bool(may_create_files),
        "expected_new_files": expected_new_files,
        "included_files": contract_entries,
        "generation_files": [
            {key: value for key, value in entry.items() if key not in {"priority", "estimated_chars", "is_edit_target"} and value is not None}
            for entry in generation_entries
        ],
        "constraints": {
            "acceptance_criteria": [str(item) for item in list(patch_plan.get("acceptance_criteria") or job_contract.get("acceptance_criteria") or [])],
            "required_tests": required_tests,
            "must_not_change": must_not_change,
            "assumptions": [str(item) for item in list(patch_plan.get("assumptions") or [])],
            "unresolved_uncertainties": [str(item) for item in list(patch_plan.get("unresolved_uncertainties") or [])],
            "execution_risks": [str(item) for item in list(patch_plan.get("risks") or [])],
        },
        "stage8_output_contract": stage8_output_contract,
        "stage9_validation_requirements": stage9_validation_requirements,
        "stage10_apply_requirements": stage10_apply_requirements,
        "stage11_static_verification": stage11_static_verification,
        "stage12_runtime_verification": stage12_runtime_verification,
        "stage13_contract_verification": stage13_contract_verification,
        "prompt_budget": prompt_budget,
    }
    retry_contract = dict(bundle_obj.get("retry_contract") or patch_plan.get("retry_contract") or {})
    if retry_contract:
        execution_contract["retry_contract"] = retry_contract
    if design_contract:
        execution_contract["design_execution_contract"] = dict(design_contract)
    if assessment_contract:
        execution_contract["assessment_report_contract"] = dict(assessment_contract)
    if retrieval_contract:
        execution_contract["retrieval_answer_contract"] = dict(retrieval_contract)
    return {
        "job_id": str(bundle_obj.get("job_id") or job_contract.get("job_id") or ""),
        "run_id": str(bundle_obj.get("run_id") or ""),
        "stage": "script_7_patch_plan",
        "source_patch_plan_artifact": source_patch_plan_artifact,
        "source_context_bundle_artifact": source_context_bundle_artifact,
        "execution_contract": execution_contract,
    }


def render_planning_review(job_id: str, run_id: str, patch_plan_obj: Mapping[str, Any], bundle_obj: Mapping[str, Any], ranked_obj: Mapping[str, Any]) -> Tuple[Dict[str, Any], str]:
    ranked_files = list(ranked_obj.get("ranked_context", {}).get("selected_files") or [])
    bundle_files = list(bundle_obj.get("context_bundle", {}).get("files") or [])
    patch_plan = dict(patch_plan_obj.get("patch_plan") or patch_plan_obj)
    review_json = {
        "job_id": job_id,
        "run_id": run_id,
        "stage": "operator_review",
        "review": {
            "objective": patch_plan.get("objective"),
            "task_archetype": _coerce_mapping(bundle_obj.get("task_profile")).get("task_archetype"),
            "output_contract_mode": patch_plan.get("output_contract_mode"),
            "selected_files": [
                {
                    "path": entry.get("path"),
                    "score": entry.get("score"),
                    "reasons": entry.get("reasons"),
                }
                for entry in ranked_files
            ],
            "bundle_summary": [
                {
                    "path": entry.get("path"),
                    "role": entry.get("role"),
                    "snippet_count": len(entry.get("snippets") or []),
                }
                for entry in bundle_files
            ],
            "patch_plan_summary": patch_plan,
            "operator_actions": ["APPROVE", "REJECT", "REVISE_AND_RERUN"],
        },
    }
    lines = [
        f"# Planning Review for {job_id}",
        "",
        f"Run: `{run_id}`",
        "",
        "## Objective",
        str(patch_plan.get("objective") or "(missing)"),
        "",
        f"Task archetype: `{_coerce_mapping(bundle_obj.get('task_profile')).get('task_archetype')}`",
        f"Output contract mode: `{patch_plan.get('output_contract_mode')}`",
        "",
        "## Ranked Files",
    ]
    for entry in ranked_files:
        reasons = "; ".join([str(x) for x in entry.get("reasons", [])])
        lines.append(f"- `{entry.get('path')}` score={entry.get('score')} :: {reasons}")
    lines.extend(["", "## Context Bundle"])
    for entry in bundle_files:
        lines.append(f"- `{entry.get('path')}` ({entry.get('role')}) snippets={len(entry.get('snippets') or [])}")
    lines.extend(["", "## Patch Plan Summary"])
    for file_entry in patch_plan.get("modify_files", []):
        lines.append(f"- `{file_entry.get('path')}` -> {', '.join(file_entry.get('intended_modifications') or [])}")
    lines.extend([
        "",
        "## Uncertainties",
        *[f"- {item}" for item in patch_plan.get("unresolved_uncertainties", [])],
        "",
        "## Operator Decision",
        "Record one of: `APPROVE`, `REJECT`, `REVISE_AND_RERUN`.",
    ])
    return review_json, "\n".join(lines).rstrip() + "\n"


def normalize_operator_decision(value: str) -> str:
    text = str(value or "").strip().upper()
    if text not in {"APPROVE", "REJECT", "REVISE_AND_RERUN"}:
        raise PipelineError("invalid_operator_decision", "decision must be APPROVE, REJECT, or REVISE_AND_RERUN", details={"decision": value}, exit_code=3)
    return text


def make_decision_artifact(job_id: str, run_id: str, decision: str, decision_reason: str, patch_plan_artifact_id: str, review_artifact_id: str, requested_revisions: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    normalized = normalize_operator_decision(decision)
    return {
        "job_id": job_id,
        "run_id": run_id,
        "stage": "operator_review",
        "result": {
            "decision": normalized,
            "decision_reason": str(decision_reason or "").strip(),
            "reviewed_patch_plan_artifact_id": str(patch_plan_artifact_id),
            "reviewed_review_artifact_id": str(review_artifact_id),
            "requested_revisions": [str(x) for x in (requested_revisions or []) if str(x).strip()],
            "timestamp": None,
        },
        "decision_digest": sha256_payload({
            "job_id": job_id,
            "run_id": run_id,
            "decision": normalized,
            "patch_plan_artifact_id": patch_plan_artifact_id,
            "review_artifact_id": review_artifact_id,
        }),
    }
