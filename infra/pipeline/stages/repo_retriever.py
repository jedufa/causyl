from __future__ import annotations
import argparse
import fnmatch
import json
import os
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from infra.pipeline.core import (
    PipelineError,
    build_envelope,
    emit_pipeline_error,
    is_path_allowed,
    load_json,
    load_pipeline_config,
    normalize_repo_relative_path,
    run_subprocess,
    save_json_atomic,
    utc_now_iso,
)
from infra.pipeline.knowledge_assets import load_knowledge_assets, resolve_knowledge_asset_paths
from infra.pipeline.schemas import validate_stage_input
from infra.pipeline.semantic import semantic_retrieve, semantic_runtime_config
from infra.pipeline.planning_ontology import classify_file_role, compatible_candidate_role, file_role_weight
from infra.pipeline.v2_foundation import (
    ensure_intake_assessment,
    ensure_retrieval_candidate_foundation,
    ensure_search_plan_foundation,
    ensure_task_profile_foundation,
)
from infra.pipeline.retrieval_policy import annotate_stage4_candidate
STAGE_KEY = "script_4_repo_retriever"
EXCLUDED_DIR_PARTS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".mypy_cache", ".pytest_cache", "jobs"}
RETRIEVAL_ASSET_NAMES = ["responsibility_terms", "passive_relevance_patterns", "recovery_signal_terms", "subsystem_map"]
LOCAL_KNOWLEDGE_REPO_ROOT = Path(__file__).resolve().parents[3]
RECOVERY_FOCUS_FLAGS = {"recovery_integrity", "stale_state_handling", "claim_integrity", "ownership_control", "idempotency"}
RECOVERY_OWNERSHIP_PATH_TERMS = {"healer", "watchdog", "recovery", "reconcile", "repair"}
MUTATION_SIGNAL_TERMS = {
    "apply_", "update", "save", "commit", "clear", "enable", "disable", "write", "persist", "mutate",
    "transition", "repair", "reassign", "kick", "execute_job", "setdefault", "write_text", "append", "create",
}
OWNERSHIP_SIGNAL_TERMS = {"claim", "lease", "ownership", "active_run_id", "active_claim", "holder", "epoch", "fencing", "reclaim", "stale claim"}
VALIDATION_SIGNAL_TERMS = {"validate", "validation", "precondition", "guard", "reject", "verify", "assert", "eligible"}
OPERATOR_SIGNAL_TERMS = {"operator", "manual", "cancel", "resume", "restart", "force", "assistant", "approval", "override"}
READ_ONLY_ANALYSIS_TERMS = {"analyze", "analysis", "inspect", "status", "summary", "report", "list", "show"}
RETRIEVAL_SCORING_SIGNAL_TERMS = {"rank_evidence", "freshness scoring", "candidate scoring", "applicability ranking", "context ranking", "retrieval scorer", "score_retrieval_candidate", "rank_evidence_by_freshness"}
MEMORY_FOCUS_FAMILIES = {"lab_assistant_memory"}
RETRIEVAL_FOCUS_FAMILIES = {"retrieval_ranking_contract"}
MOBILE_FOCUS_FAMILIES = {"mobile_android_ui", "ui_truth_projection"}
PIPELINE_FOCUS_FAMILIES = {"tool_spec_implementation", "retrieval_ranking_contract", "deterministic_patch_helper"}
CONFIG_FOCUS_FAMILIES = {"configuration_versioning", "dependency_gating", "audit_evidence"}
OPERATOR_CONTROL_FAMILIES = {"operator_control"}
SHORT_SEMANTIC_TOKENS = {"cli", "api", "ui"}
MEMORY_FOCUS_SUBSYSTEMS = {"knowledge_retrieval", "ledger_evidence", "truth_ownership", "execution_reconciliation", "task_register", "turn_transcripts", "writeback_projection"}
RETRIEVAL_FOCUS_SUBSYSTEMS = {"retrieval_runtime", "retrieval_ranker", "query_shaping", "answer_assembly"}
SCHEDULER_EXPECTED_SUBSYSTEMS = {"scheduler_core", "schedule_calculation"}
MEMORY_OWNER_PATH_TOKENS = {
    "truth_boundary": ["truth_ownership", "execution_truth_ownership", "ledger_schema", "server_lab_assistant_state"],
    "reconciliation_path": ["execution_reconciliation", "reconciliation"],
    "task_register_flow": ["task_register", "server_lab_assistant_intake"],
    "transcript_flow": ["turns", "transcript"],
    "ledger_evidence": ["ledger", "server_ledger_actions"],
    "writeback_boundary": ["writeback"],
}
OPERATOR_OWNER_PATH_TOKENS = {
    "operator_flow": ["operator_snapshot", "routing_policy", "delegation_policy", "server_ledger_actions", "backend_api_boundary"],
    "operator_cli": ["agents/", "/sessions/", "/cli.py"],
}
ORCHESTRATION_OWNER_PATH_TOKENS = {
    "recovery_responsibility": ["server_lab_assistant_recovery", "server_lab_assistant_bootstrap", "server_lab_assistant_host"],
    "dispatch_path": ["server_lab_assistant_mailbox", "server_lab_assistant_job_lifecycle", "server_lab_assistant_delegation_bindings", "server_execution_queue", "server_agents_orchestrator"],
    "state_mutation": ["server_lab_assistant_writeback"],
    "task_register_flow": ["server_lab_assistant_task_register", "server_lab_assistant_bootstrap"],
    "backend_boundary": ["backend_api_boundary"],
}
CONFIG_OWNER_PATH_TOKENS = {
    "config_policy": ["config/runtime_policy", "runtime_policy.properties", "determinism_allowlist", "allowlist.json"],
    "routing_policy_docs": ["docs/lab_assistant_routing_policy", "docs/lab_assistant_execution_truth_ownership", "docs/lab_assistant_execution_proof", "docs/lab_assistant_task_register", "lab_ops_tooling_catalog"],
    "governance_prompts": ["governance/agents/prompts/"],
}
PROJFLOW_SIGNAL_TOKENS = {"milestone", "replanning", "checkpoint", "advance", "portfolio", "capacity", "dependency"}
BOOTSTRAP_SIGNAL_TOKENS = {"bootstrap", "reconstruction", "reload", "replay", "degraded"}
QUEUE_SIGNAL_TOKENS = {"heartbeat", "timeout", "mailbox", "queue", "lifecycle", "job"}
RECOVERY_SIGNAL_TOKENS = {"recovery", "host", "takeover", "cold", "start", "reconcile", "restart"}
MOBILE_WORKFLOW_SIGNAL_TOKENS = {"chat", "workflow", "viewmodel", "reducer", "mobile", "state", "persistence"}
PROOF_SIGNAL_TOKENS = {"proof", "audit", "receipt", "result", "verification", "reporting", "strictness"}
CLI_SIGNAL_TOKENS = {"cli", "codex", "terminal", "shell", "jeff"}
TURN_SIGNAL_TOKENS = {"turn", "turns", "transcript", "reattach", "commit", "idempotency"}
SCHEDULER_SIGNAL_TOKENS = {"scheduler", "scheduled", "schedule", "watchdog", "cron", "overnight"}
PROJFLOW_STRICT_TOKENS = {"milestone", "replanning", "portfolio", "capacity", "dependency", "program"}
GENERATED_SCRIPT_PREFIXES = ("infra/bin/generate_",)
GENERATED_SCRIPT_NAMES = {"pis-dataset-extractor.py"}
WEBUI_HUB_BASENAMES = {"server_routes_and_main.py", "server_agents.py", "server_agents_orchestrator.py"}
GENERIC_DOC_NOISE_PREFIXES = ("docs/90_archive/markdown_source/persistent_files_chatgpt/",)
GENERIC_TEST_NOISE_BASENAMES = {"test_scheduled_jobs.py", "test_webui_agents_orchestration.py", "test_mobile_chat_agents.py"}
LAB_ASSISTANT_DOC_PREFIXES = ("docs/lab_assistant_",)
LAB_ASSISTANT_CONTEXT_DOC_PREFIXES = ("docs/90_archive/markdown_source/persistent_files_chatgpt/lab_",)
OPERATOR_CLI_BASENAMES = {"cli.py"}
OPERATOR_CLI_DIR_MARKERS = ("/sessions/",)
AGENT_PATH_PREFIX = "agents/"
SCHEDULER_TEST_BASENAMES = {"test_scheduled_jobs.py"}
SCHEDULER_BIN_PREFIXES = ("infra/bin/scheduler_",)
SCHEDULER_CORE_BASENAMES = {"scheduled_jobs.py"}
QUEUE_OWNER_BASENAMES = {"server_lab_assistant_mailbox.py", "server_lab_assistant_job_lifecycle.py", "server_execution_queue.py"}
RECOVERY_OWNER_BASENAMES = {"server_lab_assistant_recovery.py", "server_lab_assistant_host.py", "server_lab_assistant_bootstrap.py", "server_lab_assistant_writeback.py"}
MEMORY_OWNER_BASENAMES = {
    "server_lab_assistant_state.py",
    "server_lab_assistant_execution_truth_ownership.py",
    "server_lab_assistant_execution_reconciliation.py",
    "server_lab_assistant_task_register.py",
}
CONFIG_OWNER_BASENAMES = {"runtime_policy.properties", "determinism_allowlist.json"}
DOC_CONFIG_OWNER_BASENAMES = {
    "lab_assistant_routing_policy.md",
    "lab_assistant_execution_truth_ownership.md",
    "lab_assistant_execution_proof.md",
    "lab_assistant_task_register.md",
    "lab_ops_tooling_catalog.md",
}
PIPELINE_PLANNING_ALLOW_BASENAMES = {"planning.py", "planning_ontology.py", "test_planning_flow.py"}
BOOTSTRAP_OWNER_MARKERS = (
    "server_lab_assistant_bootstrap",
    "test_lab_assistant_bootstrap",
    "server_lab_assistant_task_register",
    "test_lab_assistant_task_register",
    "server_lab_assistant_state",
)
QUEUE_OWNER_MARKERS = (
    "server_lab_assistant_job_lifecycle",
    "server_lab_assistant_mailbox",
    "server_execution_queue",
    "test_lab_assistant_job_lifecycle",
    "test_lab_assistant_mailbox",
)
RECOVERY_OWNER_MARKERS = (
    "server_lab_assistant_recovery",
    "server_lab_assistant_host",
    "server_lab_assistant_writeback",
    "server_lab_assistant_execution_reconciliation",
    "backend_api_boundary",
    "test_lab_assistant_recovery",
)
MOBILE_OWNER_MARKERS = (
    "chatviewmodel",
    "chatmodels",
    "chatreducer",
    "httpchatapi",
    "chatviewmodelworkflowstatetest",
    "httpchatapitest",
)
TURN_OWNER_MARKERS = (
    "server_lab_assistant_turns",
    "test_lab_assistant_turns",
    "agents_chat_transcript",
    "test_agents_chat_transcript",
)
CONFIG_OWNER_MARKERS = (
    "config/runtime_policy",
    "runtime_policy.properties",
    "determinism_allowlist",
    "governance/agents/prompts/",
    "role__builder",
    "role__verifier",
    "task__ledger_bound",
    "docs/lab_assistant_routing_policy",
    "docs/lab_assistant_execution_truth_ownership",
    "docs/lab_assistant_execution_proof",
    "docs/lab_assistant_task_register",
    "lab_ops_tooling_catalog",
)
PROJFLOW_OWNER_MARKERS = (
    "tools/projflow/",
    "planning.py",
    "planning_ontology.py",
    "test_planning_flow.py",
)
AUDIT_OWNER_MARKERS = (
    "server_lab_assistant_execution_proof",
    "server_lab_assistant_operator_snapshot",
    "scheduled_jobs.py",
    "test_scheduled_jobs.py",
    "governance/agents/prompts/",
)
RETRIEVAL_OWNER_PATH_TOKENS = {
    "retrieval_owner": ["infra/pipeline/planning.py"],
    "retrieval_scoring": ["infra/pipeline/stages/context_ranker.py"],
    "retrieval_search": ["infra/pipeline/stages/search_plan.py"],
    "retrieval_answer": ["infra/pipeline/stages/repo_retriever.py", "infra/pipeline/stages/context_bundle.py"],
    "retrieval_rules": ["infra/pipeline/planning_ontology.py", "infra/pipeline/knowledge/"],
}
MEMORY_HUB_BASENAMES = {"server_routes_and_main.py", "scheduled_jobs.py", "search_plan.py", "planning.py"}
MEMORY_CONTEXT_SCAN_BASENAMES = {"server_routes_and_main.py"}
CORE_HUB_PATH_MARKERS = {
    "scheduled_jobs.py": "scheduler_hub",
    "server_routes_and_main.py": "webui_router_hub",
    "server_agents_orchestrator.py": "webui_orchestrator_hub",
    "planning.py": "pipeline_planner_hub",
    "search_plan.py": "pipeline_stage_hub",
    "agent_benchmark.py": "benchmark_harness",
}
CORE_HUB_GENERIC_TOKENS = {"agent", "agents", "benchmark", "jobs", "main", "pipeline", "plan", "routes", "scheduled", "search", "server", "webui"}
OWNER_SIGNAL_COVERS = {"state_mutation", "validation_path", "recovery_responsibility", "truth_boundary", "reconciliation_path", "task_register_flow", "transcript_flow", "ledger_evidence", "memory_retrieval", "writeback_boundary", "retrieval_owner"}
HUB_SPAN_COVERS = {"recovery_path", "recovery_responsibility", "ownership_control", "state_mutation", "operator_flow", "validation_path", "dispatch_path", "retrieval_scoring", "retrieval_search", "retrieval_answer", "retrieval_rules"}
ACTIVE_OWNER_COVERS = OWNER_SIGNAL_COVERS.union({"operator_flow", "dispatch_path"})
REQUIRED_FAILURE_COVERS = {"system_failure_modes", "recovery_failure_modes", "operator_failure_modes", "concurrency_failure_modes", "crash_failure", "persistence_failure", "resource_failure", "corruption_failure"}
RG_BATCH_LIMITS = {"literal": 12, "test_name": 12, "symbol": 8}


class _StageProgressReporter:
    def __init__(self) -> None:
        raw_path = str(os.environ.get("PIPELINE_PROGRESS_PATH") or "").strip()
        self.path = Path(raw_path) if raw_path else None
        self._last_text = ""

    def update(self, text: str, **payload: Any) -> None:
        normalized = " ".join(str(text or "").split()).strip()
        if not normalized or self.path is None or normalized == self._last_text:
            return
        self._last_text = normalized
        body = {"status_line": normalized, **payload}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8")


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
        if all(bool(exists.get(name)) for name in RETRIEVAL_ASSET_NAMES):
            return candidate
    return LOCAL_KNOWLEDGE_REPO_ROOT.resolve()
def _load_retrieval_assets(repo_root: str | Path, config: Optional[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    assets_repo_root = _resolve_assets_repo_root(repo_root, config)
    return load_knowledge_assets(assets_repo_root, config or {}, RETRIEVAL_ASSET_NAMES)
def _failure_texts(intake_assessment: Mapping[str, Any]) -> List[str]:
    taxonomy = _coerce_mapping(intake_assessment.get("failure_taxonomy"))
    out: List[str] = []
    for key in ["operator_failure_modes", "system_failure_modes", "concurrency_failure_modes", "recovery_failure_modes"]:
        out.extend(str(item) for item in _coerce_list(taxonomy.get(key)))
    return _dedupe_strings(out)
def _responsibility_catalog(asset: Mapping[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for raw in _coerce_list(asset.get("responsibilities")):
        item = _coerce_mapping(raw)
        term = str(item.get("term") or "").strip()
        if not term:
            continue
        out[term] = _dedupe_strings(item.get("clues") or [])
    return out
def _recovery_terms(asset: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for raw in _coerce_list(asset.get("signals")):
        item = _coerce_mapping(raw)
        out.extend(str(term) for term in _coerce_list(item.get("terms")))
    out.extend(["healer", "watchdog", "repair_class", "run_healer", "detect_findings", "orphan", "stale claim", "reconcile", "recovery point"])
    return _dedupe_strings(out)
def _pattern_hits(text: str, terms: Sequence[str]) -> List[str]:
    lowered = str(text or "").lower()
    return [term for term in _dedupe_strings(terms) if str(term).lower() in lowered]

def _core_hub_kind(path: str) -> str:
    basename = Path(str(path or "")).name.lower()
    return str(CORE_HUB_PATH_MARKERS.get(basename) or "")

def _matched_query_tokens(candidate: Mapping[str, Any]) -> Set[str]:
    out: Set[str] = set()
    for raw in list(candidate.get("matched_terms") or []) + list(candidate.get("matched_symbols") or []):
        for token in re.split(r"[^A-Za-z0-9]+", str(raw or "").lower()):
            token = token.strip()
            if (len(token) <= 4 and token not in SHORT_SEMANTIC_TOKENS) or token in CORE_HUB_GENERIC_TOKENS:
                continue
            out.add(token)
    return out

def _path_keyword_overlap(path: str, query_tokens: Set[str]) -> Set[str]:
    stem_tokens = {
        token.lower()
        for token in re.split(r"[^A-Za-z0-9]+", Path(str(path or "")).stem.lower())
        if len(token) > 4 and token.lower() not in CORE_HUB_GENERIC_TOKENS
    }
    return stem_tokens.intersection({str(token or "").lower() for token in query_tokens})


def _candidate_query_terms(candidate: Mapping[str, Any]) -> Set[str]:
    terms: Set[str] = set()
    for raw in list(candidate.get("matched_terms") or []) + list(candidate.get("matched_symbols") or []):
        for token in re.split(r"[^A-Za-z0-9]+", str(raw or "").lower()):
            token = token.strip()
            if len(token) >= 3:
                terms.add(token)
    return terms


def _top_subsystems(search_plan: Mapping[str, Any]) -> Set[str]:
    return {
        str(item or "").strip()
        for item in list(_coerce_mapping(search_plan).get("subsystem_hypotheses") or [])[:4]
        if str(item or "").strip()
    }


def _semantic_filename_hints(search_plan: Mapping[str, Any], task_profile: Mapping[str, Any]) -> List[str]:
    families = {str(item or "").strip() for item in _coerce_list(task_profile.get("task_families")) if str(item or "").strip()}
    query_tokens = _semantic_query_tokens(search_plan, task_profile)
    top_subsystems = _top_subsystems(search_plan)
    hints: List[str] = []
    if query_tokens.intersection(BOOTSTRAP_SIGNAL_TOKENS) or top_subsystems.intersection({"task_register", "checkpoint_resume"}):
        hints.extend(["server_lab_assistant_bootstrap", "test_lab_assistant_bootstrap", "server_lab_assistant_task_register", "test_lab_assistant_task_register", "server_lab_assistant_state"])
    if query_tokens.intersection(QUEUE_SIGNAL_TOKENS) or top_subsystems.intersection({"queue_dispatch", "heartbeat_liveness"}):
        hints.extend(["server_lab_assistant_job_lifecycle", "server_lab_assistant_mailbox", "server_execution_queue", "test_lab_assistant_job_lifecycle", "test_lab_assistant_mailbox"])
    if query_tokens.intersection(RECOVERY_SIGNAL_TOKENS) or top_subsystems.intersection({"recovery_mechanisms", "checkpoint_resume"}):
        hints.extend(["server_lab_assistant_recovery", "server_lab_assistant_host", "server_lab_assistant_writeback", "server_lab_assistant_execution_reconciliation", "backend_api_boundary", "test_lab_assistant_recovery"])
    if families.intersection(MOBILE_FOCUS_FAMILIES) or query_tokens.intersection(MOBILE_WORKFLOW_SIGNAL_TOKENS):
        hints.extend(["ChatViewModel", "ChatModels", "ChatReducer", "HttpChatApi", "ChatViewModelWorkflowStateTest", "HttpChatApiTest"])
    if families.intersection(CONFIG_FOCUS_FAMILIES) or top_subsystems.intersection({"dependency_gating", "configuration_versioning", "audit_receipts", "operator_entrypoints"}):
        hints.extend(["runtime_policy.properties", "determinism_allowlist", "role__builder", "role__verifier", "task__ledger_bound", "lab_assistant_routing_policy", "lab_assistant_execution_truth_ownership", "lab_assistant_execution_proof", "lab_assistant_task_register", "lab_ops_tooling_catalog"])
    if query_tokens.intersection(PROJFLOW_STRICT_TOKENS) or (top_subsystems.intersection({"agent_orchestration"}) and query_tokens.intersection(PROJFLOW_STRICT_TOKENS)):
        hints.extend(["models.py", "stages.py", "runner.py", "planning.py", "planning_ontology.py", "test_planning_flow.py"])
    if families.intersection(OPERATOR_CONTROL_FAMILIES) or top_subsystems.intersection({"operator_entrypoints"}) or query_tokens.intersection(CLI_SIGNAL_TOKENS):
        hints.extend(["cli.py", "backend_api_boundary.py", "sessions/"])
    if query_tokens.intersection(PROOF_SIGNAL_TOKENS) or top_subsystems.intersection({"audit_receipts"}):
        hints.extend(["server_lab_assistant_execution_proof", "server_lab_assistant_operator_snapshot", "scheduled_jobs.py", "test_scheduled_jobs.py"])
    if query_tokens.intersection(TURN_SIGNAL_TOKENS) or top_subsystems.intersection({"turn_transcripts", "state_persistence"}):
        hints.extend(["server_lab_assistant_turns", "test_lab_assistant_turns", "agents_chat_transcript", "test_agents_chat_transcript"])
    return _dedupe_strings(hints)


def _apply_semantic_owner_boost(
    candidate: Dict[str, Any],
    *,
    task_profile: Mapping[str, Any],
    search_plan: Mapping[str, Any],
) -> None:
    path = str(candidate.get("path") or "").replace("\\", "/").lower()
    basename = Path(path).name
    families = {str(item or "").strip() for item in _coerce_list(task_profile.get("task_families")) if str(item or "").strip()}
    query_tokens = _semantic_query_tokens(search_plan, task_profile)
    top_subsystems = _top_subsystems(search_plan)
    covers = set(_coerce_list(candidate.get("covers")))
    bonus = 0.0
    reasons: List[str] = []
    if basename.startswith("test_") and families.intersection({"scheduler_safety", "workflow_recovery"}) and "testing_verification" not in families:
        return

    if (query_tokens.intersection(BOOTSTRAP_SIGNAL_TOKENS) or top_subsystems.intersection({"task_register", "checkpoint_resume"})) and any(marker in path for marker in BOOTSTRAP_OWNER_MARKERS):
        bonus += 2.7
        reasons.append("semantic_bootstrap_owner")
    if (query_tokens.intersection(QUEUE_SIGNAL_TOKENS) or top_subsystems.intersection({"queue_dispatch", "heartbeat_liveness"})) and any(marker in path for marker in QUEUE_OWNER_MARKERS):
        bonus += 3.1
        reasons.append("semantic_queue_owner")
    if (query_tokens.intersection(RECOVERY_SIGNAL_TOKENS) or top_subsystems.intersection({"recovery_mechanisms", "checkpoint_resume"})) and any(marker in path for marker in RECOVERY_OWNER_MARKERS):
        bonus += 2.9
        reasons.append("semantic_recovery_owner")
    if (families.intersection(MOBILE_FOCUS_FAMILIES) or query_tokens.intersection(MOBILE_WORKFLOW_SIGNAL_TOKENS)) and any(marker in path for marker in MOBILE_OWNER_MARKERS):
        bonus += 3.0
        reasons.append("semantic_mobile_owner")
    if (families.intersection(CONFIG_FOCUS_FAMILIES) or top_subsystems.intersection({"dependency_gating", "configuration_versioning", "audit_receipts", "operator_entrypoints"})) and any(marker in path for marker in CONFIG_OWNER_MARKERS):
        bonus += 3.1
        reasons.append("semantic_config_owner")
        if basename in DOC_CONFIG_OWNER_BASENAMES:
            bonus += 1.8
            reasons.append("semantic_config_doc_owner")
        if "governance/agents/prompts/" in path:
            bonus += 2.1
            reasons.append("semantic_prompt_owner")
    if (query_tokens.intersection(PROJFLOW_STRICT_TOKENS) or (top_subsystems.intersection({"agent_orchestration"}) and query_tokens.intersection(PROJFLOW_STRICT_TOKENS))) and any(marker in path for marker in PROJFLOW_OWNER_MARKERS):
        bonus += 2.9
        reasons.append("semantic_projflow_owner")
    if (query_tokens.intersection(PROOF_SIGNAL_TOKENS) or top_subsystems.intersection({"audit_receipts"})) and any(marker in path for marker in AUDIT_OWNER_MARKERS):
        bonus += 2.5
        reasons.append("semantic_audit_owner")
    if (families.intersection(OPERATOR_CONTROL_FAMILIES) or top_subsystems.intersection({"operator_entrypoints"}) or query_tokens.intersection(CLI_SIGNAL_TOKENS)) and (
        path.startswith(AGENT_PATH_PREFIX) or basename in OPERATOR_CLI_BASENAMES or any(marker in path for marker in OPERATOR_CLI_DIR_MARKERS)
    ):
        bonus += 3.4
        reasons.append("semantic_operator_owner")
        if path.startswith(AGENT_PATH_PREFIX) or basename in OPERATOR_CLI_BASENAMES:
            bonus += 2.4
            reasons.append("semantic_operator_cli_owner")
    if (query_tokens.intersection(TURN_SIGNAL_TOKENS) or top_subsystems.intersection({"turn_transcripts", "state_persistence"})) and any(marker in path for marker in TURN_OWNER_MARKERS):
        bonus += 3.0
        reasons.append("semantic_turn_owner")
    if basename == "scheduled_jobs.py" and not (top_subsystems.intersection(SCHEDULER_EXPECTED_SUBSYSTEMS) or query_tokens.intersection(SCHEDULER_SIGNAL_TOKENS)):
        bonus -= 2.6
        reasons.append("semantic_scheduler_hub_penalty")
    if basename in SCHEDULER_TEST_BASENAMES and not (top_subsystems.intersection(SCHEDULER_EXPECTED_SUBSYSTEMS) or query_tokens.intersection(SCHEDULER_SIGNAL_TOKENS)):
        bonus -= 1.9
        reasons.append("semantic_scheduler_test_penalty")
    if basename in PIPELINE_PLANNING_ALLOW_BASENAMES and not query_tokens.intersection(PROJFLOW_STRICT_TOKENS.union({"projflow"})) and not families.intersection(PIPELINE_FOCUS_FAMILIES):
        bonus -= 1.8
        reasons.append("semantic_pipeline_hub_penalty")
    if bonus <= 0.0:
        return
    if basename.startswith("test_"):
        bonus *= 0.7 if any(tag in reasons for tag in {"semantic_bootstrap_owner", "semantic_queue_owner", "semantic_recovery_owner", "semantic_turn_owner"}) else 0.45
    if basename in WEBUI_HUB_BASENAMES and not covers.intersection(ACTIVE_OWNER_COVERS):
        bonus *= 0.35
    scores = _coerce_mapping(candidate.get("scores"))
    scores["graph_score"] = round(float(scores.get("graph_score") or 0.0) + bonus, 4)
    candidate["scores"] = dict(scores)
    candidate["reason_codes"] = _dedupe_strings(list(candidate.get("reason_codes") or []) + reasons)


def _candidate_has_direct_path_request(candidate: Mapping[str, Any], path: str) -> bool:
    reason_codes = {str(code or "").strip() for code in _coerce_list(candidate.get("reason_codes")) if str(code or "").strip()}
    if "path_hint" in reason_codes:
        return True
    rel = str(path or "").replace("\\", "/").lower().strip("/")
    if not rel:
        return False
    basename = Path(rel).name
    for raw in list(candidate.get("matched_terms") or []) + list(candidate.get("matched_symbols") or []):
        text = str(raw or "").strip().lower()
        if not text or ("/" not in text and "." not in text):
            continue
        text_name = Path(text).name
        if text_name and (basename == text_name or rel.endswith(text_name) or text in rel):
            return True
    return False
SEMANTIC_CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".java", ".kt", ".rs", ".sh", ".ps1", ".html", ".css", ".scss", ".sass"}
SEMANTIC_CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml"}
SEMANTIC_DOC_EXTENSIONS = {".md", ".txt", ".rst"}
SEMANTIC_GENERATED_DOC_PREFIXES = (
    "infra/pipeline/knowledge_drafts/",
    "tests/benchmarks/pipeline/",
    "governance/ledger/",
    "runtime/reports/",
    "reports/",
)
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
SEMANTIC_HYBRID_FAMILIES = {
    "lab_assistant_memory",
    "assistant_truth_and_identity",
    "orchestration_checkpointing",
    "ui_truth_projection",
    "retrieval_ranking_contract",
}
PIPELINE_HYBRID_ALLOW_FAMILIES = {"lab_assistant_memory", "assistant_truth_and_identity", "retrieval_ranking_contract"}
PIPELINE_HYBRID_SIGNAL_TOKENS = {"memory", "task", "register", "truth", "ownership", "reconciliation", "ledger", "transcript", "provenance", "freshness"}
SEMANTIC_DOC_FAMILIES = {"documentation_contracts"}
SEMANTIC_DOC_TASK_TYPES = {"docs", "documentation"}
SEMANTIC_HYBRID_TASK_TYPES = {"truth_contract", "research", "analysis", "architecture", "design"}

def _semantic_path_category(path: str) -> str:
    rel = str(path or "").replace("\\", "/").lower()
    suffix = Path(rel).suffix.lower()
    if rel.startswith("tests/") or "/tests/" in rel:
        return "test"
    if any(rel.startswith(prefix) for prefix in SEMANTIC_GENERATED_DOC_PREFIXES):
        return "generated_doc"
    if suffix in SEMANTIC_CODE_EXTENSIONS:
        return "code"
    if suffix in SEMANTIC_CONFIG_EXTENSIONS:
        return "config"
    if suffix in SEMANTIC_DOC_EXTENSIONS:
        return "doc"
    return "other"


def _is_contamination_surface(path: str) -> bool:
    rel = str(path or "").replace("\\", "/").lower()
    return any(marker in rel for marker in CONTAMINATION_PATH_MARKERS)


def _semantic_task_profile(task_profile: Mapping[str, Any]) -> str:
    primary = str(task_profile.get("primary_task_type") or "").strip().lower()
    families = {str(item or "").strip() for item in _coerce_list(task_profile.get("task_families")) if str(item or "").strip()}
    if primary in SEMANTIC_DOC_TASK_TYPES or (families.intersection(SEMANTIC_DOC_FAMILIES) and not families.intersection(SEMANTIC_HYBRID_FAMILIES)):
        return "docs"
    if primary in SEMANTIC_HYBRID_TASK_TYPES or families.intersection(SEMANTIC_HYBRID_FAMILIES):
        return "hybrid"
    return "code"


def _is_docs_task_profile(task_profile: Mapping[str, Any]) -> bool:
    primary = str(task_profile.get("primary_task_type") or "").strip().lower()
    families = {str(item or "").strip() for item in _coerce_list(task_profile.get("task_families")) if str(item or "").strip()}
    return primary in SEMANTIC_DOC_TASK_TYPES or bool(families.intersection(SEMANTIC_DOC_FAMILIES))


def _is_non_implementation_governance_surface(path: str) -> bool:
    rel = str(path or "").replace("\\", "/").lower()
    return (
        rel.startswith("governance/ledger/patch_instruction_sheets/")
        or rel.startswith("governance/ledger/gold_items/")
        or rel.startswith("governance/ledger/gold_items_large/")
        or rel.startswith("governance/ledger/__gold_benchmark_gpt_items/")
    )


def _should_exclude_candidate_path(path: str, task_profile: Mapping[str, Any]) -> bool:
    rel = str(path or "").replace("\\", "/").strip()
    if not rel:
        return True
    if _is_contamination_surface(rel):
        return True
    if _is_non_implementation_governance_surface(rel) and not _is_docs_task_profile(task_profile):
        return True
    return False


def _semantic_path_prior(path: str, task_profile: Mapping[str, Any]) -> float:
    category = _semantic_path_category(path)
    profile = _semantic_task_profile(task_profile)
    contamination = _is_contamination_surface(path)
    if profile == "docs":
        table = {"code": 0.95, "test": 0.85, "config": 0.9, "doc": 1.0, "generated_doc": 0.78, "other": 0.9}
    elif profile == "hybrid":
        table = {"code": 1.15, "test": 1.05, "config": 1.0, "doc": 0.92, "generated_doc": 0.58, "other": 0.9}
    else:
        table = {"code": 1.25, "test": 1.12, "config": 1.04, "doc": 0.72, "generated_doc": 0.38, "other": 0.86}
    prior = float(table.get(category, 0.9))
    if contamination:
        prior *= 0.45 if profile == "docs" else 0.12
    return prior


def _semantic_query_tokens(search_plan: Mapping[str, Any], task_profile: Mapping[str, Any]) -> Set[str]:
    parts: List[str] = [str(search_plan.get("semantic_query_text") or "")]
    for query in _coerce_list(search_plan.get("queries")):
        item = _coerce_mapping(query)
        parts.append(str(item.get("query") or ""))
    parts.extend(str(item) for item in _coerce_list(search_plan.get("subsystem_hypotheses")))
    parts.extend(str(item) for item in _coerce_list(_coerce_mapping(task_profile.get("retrieval_bias")).get("include")))
    tokens: Set[str] = set()
    for part in parts:
        for token in re.split(r"[^A-Za-z0-9]+", str(part or "").lower()):
            token = token.strip()
            if len(token) <= 4 or token in CORE_HUB_GENERIC_TOKENS:
                continue
            tokens.add(token)
    return tokens


def _score_semantic_expansion_path(
    rel: str,
    *,
    query_tokens: Set[str],
    hints: Sequence[str],
    anchor_dirs: Set[str],
    top_subsystems: Set[str],
    task_profile: Mapping[str, Any],
) -> float:
    rel_lower = str(rel or "").lower()
    basename = Path(rel_lower).name
    parent = Path(rel_lower).parent.as_posix().lower()
    category = _semantic_path_category(rel_lower)
    families = {str(item or "").strip() for item in _coerce_list(task_profile.get("task_families")) if str(item or "").strip()}
    task_archetype = str(task_profile.get("task_archetype") or "implementation_change")
    file_role = classify_file_role(rel_lower, "")
    base = {
        "code": 2.15,
        "test": 1.75,
        "config": 1.25,
        "doc": 0.65,
        "generated_doc": -0.25,
        "other": 0.2,
    }.get(category, 0.2)
    role_multiplier = file_role_weight(task_archetype, file_role, "retrieval")
    score = base * _semantic_path_prior(rel_lower, task_profile) * max(0.18, role_multiplier)
    if any(h.lower() in rel_lower for h in hints):
        score += 2.0
    if rel_lower.startswith("tools/projflow/") and query_tokens.intersection(PROJFLOW_SIGNAL_TOKENS):
        score += 2.4
    if "bootstrap" in rel_lower and query_tokens.intersection(BOOTSTRAP_SIGNAL_TOKENS):
        score += 2.3
    if basename in QUEUE_OWNER_BASENAMES and query_tokens.intersection(QUEUE_SIGNAL_TOKENS):
        score += 2.4
    if basename in RECOVERY_OWNER_BASENAMES and query_tokens.intersection(RECOVERY_SIGNAL_TOKENS):
        score += 2.1
    if rel_lower.startswith("infra/mobile/android/app/src/main/java/com/eiel/chat/") and query_tokens.intersection(MOBILE_WORKFLOW_SIGNAL_TOKENS):
        score += 2.2
    if families.intersection(CONFIG_FOCUS_FAMILIES):
        if rel_lower.startswith("config/") or basename in CONFIG_OWNER_BASENAMES:
            score += 2.2
        if basename in DOC_CONFIG_OWNER_BASENAMES or any(rel_lower.startswith(prefix) for prefix in LAB_ASSISTANT_DOC_PREFIXES):
            score += 1.8
        if "governance/agents/prompts/" in rel_lower:
            score += 1.4
    if families.intersection(OPERATOR_CONTROL_FAMILIES):
        if rel_lower.startswith(AGENT_PATH_PREFIX) or basename in OPERATOR_CLI_BASENAMES or any(marker in rel_lower for marker in OPERATOR_CLI_DIR_MARKERS):
            score += 2.4
    if query_tokens:
        overlap = _path_keyword_overlap(rel_lower, query_tokens)
        score += 0.8 * len(overlap)
        for token in query_tokens:
            if token in basename:
                score += 0.45
            elif token in rel_lower:
                score += 0.18
    if top_subsystems and any(str(sub or "").lower() in rel_lower for sub in top_subsystems):
        score += 0.75
    if anchor_dirs:
        if parent in anchor_dirs:
            score += 0.9
        elif any(parent.startswith(anchor + "/") or anchor.startswith(parent + "/") for anchor in anchor_dirs):
            score += 0.45
    if _is_contamination_surface(rel_lower) and _semantic_task_profile(task_profile) != "docs":
        score -= 1.4
    if category == "generated_doc" and _semantic_task_profile(task_profile) != "docs":
        score -= 0.55
    return round(score, 4)


def _ordered_deterministic_paths_for_semantic(candidates: Mapping[str, Mapping[str, Any]], search_plan: Mapping[str, Any]) -> List[str]:
    coverage_lookup = _query_coverage_lookup(search_plan)
    top_subsystems = {str(item or "").strip() for item in list(search_plan.get("subsystem_hypotheses") or [])[:3] if str(item or "").strip()}
    ranked: List[Tuple[float, str]] = []
    for path, candidate in candidates.items():
        ranked.append((_rough_candidate_prefilter_score(candidate, coverage_lookup, top_subsystems), str(path)))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [path for _, path in ranked]
class _GitIgnoreMatcher:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._rules = self._load_rules(repo_root)
    @staticmethod
    def _load_rules(repo_root: Path) -> List[Dict[str, Any]]:
        ignore_file = repo_root / ".gitignore"
        if not ignore_file.is_file():
            return []
        rules: List[Dict[str, Any]] = []
        for raw in ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            negated = line.startswith("!")
            if negated:
                line = line[1:].strip()
            if not line:
                continue
            directory_only = line.endswith("/")
            if directory_only:
                line = line[:-1]
            anchored = line.startswith("/")
            if anchored:
                line = line[1:]
            pattern = line.replace("\\", "/")
            if not pattern:
                continue
            rules.append(
                {
                    "pattern": pattern,
                    "negated": negated,
                    "directory_only": directory_only,
                    "anchored": anchored,
                    "has_slash": "/" in pattern,
                }
            )
        return rules
    @staticmethod
    def _path_variants(rel: str) -> List[str]:
        rel = rel.replace("\\", "/").strip("/")
        if not rel:
            return []
        parts = rel.split("/")
        variants = [rel]
        for idx in range(1, len(parts)):
            variants.append("/".join(parts[idx:]))
        return variants
    @lru_cache(maxsize=16384)
    def is_ignored(self, rel: str) -> bool:
        rel = rel.replace("\\", "/").strip("/")
        if not rel or not self._rules:
            return False
        ignored = False
        basename = Path(rel).name
        for rule in self._rules:
            pattern = str(rule["pattern"])
            matched = False
            if rule["directory_only"]:
                prefix = pattern.rstrip("/")
                if rule["anchored"]:
                    matched = rel == prefix or rel.startswith(prefix + "/")
                else:
                    matched = any(variant == prefix or variant.startswith(prefix + "/") for variant in self._path_variants(rel))
            elif rule["anchored"]:
                matched = fnmatch.fnmatchcase(rel, pattern)
            elif rule["has_slash"]:
                matched = any(fnmatch.fnmatchcase(variant, pattern) for variant in self._path_variants(rel))
            else:
                matched = fnmatch.fnmatchcase(basename, pattern)
            if matched:
                ignored = not bool(rule["negated"])
        return ignored
def _is_historical_or_generated_artifact(rel: str) -> bool:
    rel_l = rel.lower()
    artifact_markers = [
        "/artifacts/",
        "/runs/",
        "artifact_registry.json",
        "job_state.json",
        "pipeline_run_summary.json",
        "events.jsonl",
        "jobs.jsonl",
        "session__",
        "reports/go-",
        "reports/projflow-",
        "reports/full-auto-",
        "selected_files.txt",
        "/summary.json",
        "/summary.md",
    ]
    if any(marker in rel_l for marker in artifact_markers):
        return True
    return rel_l.startswith("tmp/")
def _matches_negative_target(rel: str, patterns: List[str]) -> bool:
    rel_norm = str(rel or "").replace("\\", "/").strip("/")
    rel_lower = rel_norm.lower()
    basename = Path(rel_norm).name.lower()
    for raw in patterns:
        pattern = str(raw or "").replace("\\", "/").strip()
        if not pattern:
            continue
        lower = pattern.lower()
        if lower.endswith("/"):
            prefix = lower.strip("/")
            if rel_lower == prefix or rel_lower.startswith(prefix + "/"):
                return True
            continue
        if any(ch in lower for ch in "*?[]"):
            if fnmatch.fnmatchcase(rel_lower, lower) or fnmatch.fnmatchcase(basename, lower):
                return True
            continue
        if rel_lower == lower or basename == lower:
            return True
        if "/" in lower and rel_lower.endswith(lower):
            return True
    return False
def _iter_filename_hits(repo_root: Path, hints: List[str], ignore_matcher: Optional[_GitIgnoreMatcher] = None) -> List[str]:
    out: List[str] = []
    seen = set()
    files = [p for p in repo_root.rglob("*") if p.is_file()]
    for hint in hints:
        raw_hint = str(hint or "").replace("\\", "/").strip()
        name = Path(raw_hint).name.lower()
        if not name:
            continue
        exact_name = "." in name
        path_fragment = raw_hint.lower() if "/" in raw_hint else ""
        for match in files:
            rel = match.relative_to(repo_root).as_posix()
            if ignore_matcher and ignore_matcher.is_ignored(rel):
                continue
            rel_lower = rel.lower()
            base_lower = Path(rel).name.lower()
            is_match = base_lower == name or rel_lower.endswith(name)
            if not is_match and not exact_name:
                is_match = name in base_lower or (path_fragment and path_fragment in rel_lower)
            if not is_match:
                continue
            if rel not in seen:
                seen.add(rel)
                out.append(rel)
    return out
def _collect_rg_hits(repo_root: Path, rg_path: str, query: str, query_type: str, timeout_seconds: int, *, include_docs_for_symbol: bool = False) -> List[Dict[str, Any]]:
    if query_type == "filename":
        return []
    args = [rg_path, "--line-number", "--with-filename", "--color", "never"]
    if query_type in {"literal", "test_name"}:
        args.extend(["-F", "-i"])
    elif query_type == "symbol":
        args.extend(["-i"])
        globs = ["*.py", "*.js", "*.ts", "*.tsx", "*.jsx", "*.go", "*.java", "*.kt", "*.rs", "*.sh", "*.ps1", "*.json", "*.yaml", "*.yml", "*.toml"]
        if include_docs_for_symbol:
            globs.extend(["*.md", "*.txt", "*.rst"])
        for pattern in globs:
            args.extend(["-g", pattern])
    args.append(str(query))
    args.append(".")
    result = run_subprocess(args, cwd=repo_root, timeout_seconds=timeout_seconds)
    if result.returncode not in {0, 1}:
        raise PipelineError("repository_access_error", "ripgrep failed", details=result.to_dict(), exit_code=4)
    hits: List[Dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        rel_path, line_no, preview = parts
        try:
            parsed_line = int(line_no)
        except ValueError:
            continue
        hits.append({"path": rel_path.replace("\\", "/"), "line": parsed_line, "preview": preview[:300]})
    return hits

def _normalize_search_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9_]+", str(value or "").lower()))

def _batch_queries(queries: Sequence[str], batch_size: int) -> List[List[str]]:
    query_list = _dedupe_strings(queries)
    if not query_list:
        return []
    return [query_list[idx:idx + max(1, batch_size)] for idx in range(0, len(query_list), max(1, batch_size))]

def _match_queries_for_hit(preview: str, path: str, queries: Sequence[str], query_type: str) -> List[str]:
    preview_lower = str(preview or "").lower()
    preview_norm = _normalize_search_text(preview)
    path_lower = str(path or "").lower()
    path_norm = _normalize_search_text(path)
    collapsed_preview = re.sub(r"[^a-z0-9_]+", "", preview_lower)
    out: List[str] = []
    for query in _dedupe_strings(queries):
        direct = str(query or "").strip().lower()
        normalized = _normalize_search_text(query)
        collapsed = re.sub(r"[^a-z0-9_]+", "", direct)
        if not direct:
            continue
        matched = direct in preview_lower or direct in path_lower
        if not matched and normalized:
            matched = normalized in preview_norm or normalized in path_norm
        if not matched and query_type == "symbol" and collapsed:
            matched = collapsed in collapsed_preview
        if matched:
            out.append(str(query))
    if not out and len(list(queries)) == 1:
        return [str(list(queries)[0])]
    return out

def _collect_rg_hits_batched(
    repo_root: Path,
    rg_path: str,
    queries: Sequence[str],
    query_type: str,
    timeout_seconds: int,
    *,
    include_docs_for_symbol: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    if query_type == "filename":
        return [], 0
    hits: List[Dict[str, Any]] = []
    batches_run = 0
    batch_size = int(RG_BATCH_LIMITS.get(query_type, 10))
    for batch in _batch_queries(queries, batch_size):
        args = [rg_path, "--line-number", "--with-filename", "--color", "never"]
        if query_type in {"literal", "test_name"}:
            args.extend(["-F", "-i"])
        elif query_type == "symbol":
            args.extend(["-i"])
            globs = ["*.py", "*.js", "*.ts", "*.tsx", "*.jsx", "*.go", "*.java", "*.kt", "*.rs", "*.sh", "*.ps1", "*.json", "*.yaml", "*.yml", "*.toml"]
            if include_docs_for_symbol:
                globs.extend(["*.md", "*.txt", "*.rst"])
            for pattern in globs:
                args.extend(["-g", pattern])
        for query in batch:
            args.extend(["-e", str(query)])
        args.append(".")
        result = run_subprocess(args, cwd=repo_root, timeout_seconds=timeout_seconds)
        batches_run += 1
        if result.returncode not in {0, 1}:
            raise PipelineError("repository_access_error", "ripgrep failed", details=result.to_dict(), exit_code=4)
        for line in result.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            rel_path, line_no, preview = parts
            try:
                parsed_line = int(line_no)
            except ValueError:
                continue
            matched_queries = _match_queries_for_hit(preview, rel_path, batch, query_type)
            for query in matched_queries:
                hits.append({"path": rel_path.replace("\\", "/"), "line": parsed_line, "preview": preview[:300], "query": query})
    return hits, batches_run

def _collect_filename_hits_by_query(repo_root: Path, hints: Sequence[str], ignore_matcher: Optional[_GitIgnoreMatcher] = None) -> Tuple[Dict[str, List[str]], int]:
    prepared: List[Tuple[str, str, bool, str]] = []
    for hint in _dedupe_strings(hints):
        raw_hint = str(hint or "").replace("\\", "/").strip()
        name = Path(raw_hint).name.lower()
        if not name:
            continue
        prepared.append((raw_hint, name, "." in name, raw_hint.lower() if "/" in raw_hint else ""))
    if not prepared:
        return {}, 0
    files = [p for p in repo_root.rglob("*") if p.is_file()]
    matches: Dict[str, List[str]] = {hint: [] for hint, _, _, _ in prepared}
    seen: Set[Tuple[str, str]] = set()
    for match in files:
        rel = match.relative_to(repo_root).as_posix()
        if ignore_matcher and ignore_matcher.is_ignored(rel):
            continue
        rel_lower = rel.lower()
        base_lower = Path(rel).name.lower()
        for raw_hint, name, exact_name, path_fragment in prepared:
            is_match = base_lower == name or rel_lower.endswith(name)
            if not is_match and not exact_name:
                is_match = name in base_lower or (path_fragment and path_fragment in rel_lower)
            if not is_match:
                continue
            key = (raw_hint, rel)
            if key in seen:
                continue
            seen.add(key)
            matches.setdefault(raw_hint, []).append(rel)
    return matches, 1

def _query_coverage_lookup(search_plan: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(key or "").strip().lower(): _coerce_mapping(value) for key, value in _coerce_mapping(search_plan.get("query_coverage_map")).items()}

def _candidate_subsystems(candidate: Mapping[str, Any], coverage_lookup: Mapping[str, Mapping[str, Any]]) -> Set[str]:
    out: Set[str] = set()
    for raw in list(candidate.get("matched_terms") or []) + list(candidate.get("matched_symbols") or []):
        meta = _coerce_mapping(coverage_lookup.get(str(raw or "").strip().lower()))
        out.update(str(value or "").strip() for value in _coerce_list(meta.get("subsystems") or meta.get("subsystem")) if str(value or "").strip())
    return out


def _subsystem_entries(asset: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [_coerce_mapping(item) for item in _coerce_list(_coerce_mapping(asset).get("subsystems")) if _coerce_mapping(item)]


@lru_cache(maxsize=8)
def _subsystem_registry_cached(asset_json: str) -> Dict[str, Dict[str, Any]]:
    try:
        asset = json.loads(asset_json)
    except Exception:
        asset = {}
    out: Dict[str, Dict[str, Any]] = {}
    for raw in _subsystem_entries(_coerce_mapping(asset)):
        key = str(raw.get("key") or "").strip()
        if key:
            out[key] = dict(raw)
    return out


def _subsystem_registry(asset: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    try:
        asset_json = json.dumps(_coerce_mapping(asset), sort_keys=True)
    except Exception:
        asset_json = "{}"
    return _subsystem_registry_cached(asset_json)


def _stage4_subsystem_structural_hints(
    search_plan: Mapping[str, Any],
    task_profile: Mapping[str, Any],
    subsystem_asset: Mapping[str, Any],
    *,
    limit: int = 24,
) -> List[str]:
    registry = _subsystem_registry(subsystem_asset)
    subsystems = [str(item or "").strip() for item in list(search_plan.get("subsystem_hypotheses") or [])[:4] if str(item or "").strip()]
    hints: List[str] = []
    for subsystem in subsystems:
        entry = _coerce_mapping(registry.get(subsystem))
        if not entry:
            continue
        hints.extend(str(item) for item in _coerce_list(entry.get("filename_aliases"))[:4])
        hints.extend(str(item) for item in _coerce_list(entry.get("path_clues"))[:3])
        for alias in _coerce_list(entry.get("aliases"))[:3]:
            for token in re.split(r"[^A-Za-z0-9_]+", str(alias or "").lower()):
                token = token.strip()
                if len(token) >= 5:
                    hints.append(token)
    families = {str(item or "").strip() for item in _coerce_list(task_profile.get("task_families")) if str(item or "").strip()}
    if families.intersection({"scheduler_safety", "manual_backfill", "reconciliation_recovery"}):
        hints.extend(["/scheduler", "/queue", "test_"])
    if families.intersection({"lab_assistant_memory", "stateful_workflow"}):
        hints.extend(["/store", "/repository", "/state", "transcript"])
    if families.intersection({"ui_truth_projection", "mobile_android_ui"}):
        hints.extend(["viewmodel", "reducer", "/ui", "/screen"])
    return _dedupe_strings(hints)[: max(1, int(limit or 24))]


def _seed_tokens_for_path(rel_path: str) -> Set[str]:
    tokens: Set[str] = set()
    for token in re.split(r"[^A-Za-z0-9]+", Path(str(rel_path or "")).stem.lower()):
        token = token.strip()
        if len(token) >= 4:
            tokens.add(token)
    return tokens


def _expand_structural_neighbor_paths(
    repo_root: Path,
    normalized: Sequence[Mapping[str, Any]],
    *,
    ignore_matcher: Optional[_GitIgnoreMatcher] = None,
    limit: int = 16,
) -> List[str]:
    seeds = [
        entry for entry in normalized
        if str(entry.get("candidate_intent_lane") or "") == "edit_likely"
        and not str(entry.get("hub_family") or "")
        and float((entry.get("scores") or {}).get("retrieval_sort_score") or (entry.get("scores") or {}).get("final_score") or 0.0) >= 1.5
    ]
    seed_dirs = {str(Path(str(entry.get("path") or "")).parent) for entry in seeds if str(entry.get("path") or "").strip()}
    seed_tokens = set().union(*[_seed_tokens_for_path(str(entry.get("path") or "")) for entry in seeds]) if seeds else set()
    if not seed_dirs:
        return []
    scored: List[Tuple[Tuple[int, int, int, str], str]] = []
    seen: Set[str] = {str(entry.get("path") or "") for entry in normalized}
    for seed_dir in sorted(seed_dirs):
        abs_dir = (repo_root / seed_dir).resolve()
        if not abs_dir.exists() or not abs_dir.is_dir():
            continue
        try:
            children = list(abs_dir.iterdir())
        except Exception:
            continue
        for child in children:
            if not child.is_file():
                continue
            rel = child.relative_to(repo_root).as_posix()
            if rel in seen:
                continue
            if ignore_matcher and ignore_matcher.is_ignored(rel):
                continue
            if any(part in EXCLUDED_DIR_PARTS for part in Path(rel).parts):
                continue
            role = classify_file_role(rel, "")
            overlap = len(_seed_tokens_for_path(rel).intersection(seed_tokens))
            is_test_or_config = role in {"test_file", "config_file"}
            is_doc = role in {"documentation", "governance_doc"}
            if overlap <= 0 and not is_test_or_config and not is_doc:
                continue
            priority = (
                0 if role == "test_file" else 1 if role == "config_file" else 2 if role == "implementation_code" else 3,
                0 if overlap > 0 else 1,
                len(Path(rel).parts),
                rel,
            )
            scored.append((priority, rel))
    scored.sort(key=lambda item: item[0])
    return [rel for _, rel in scored[: max(1, int(limit or 16))]]

def _rough_candidate_prefilter_score(candidate: Mapping[str, Any], coverage_lookup: Mapping[str, Mapping[str, Any]], top_subsystems: Set[str]) -> float:
    scores = _coerce_mapping(candidate.get("scores"))
    lexical = float(scores.get("lexical_score") or 0.0)
    structural = float(scores.get("structural_score") or 0.0)
    graph = float(scores.get("graph_score") or 0.0)
    semantic = float(scores.get("semantic_score") or 0.0)
    reason_codes = {str(code or "").strip() for code in _coerce_list(candidate.get("reason_codes")) if str(code or "").strip()}
    matched_terms = _dedupe_strings(list(candidate.get("matched_terms") or []) + list(candidate.get("matched_symbols") or []))
    matched_subsystems = _candidate_subsystems(candidate, coverage_lookup)
    file_role = str(candidate.get("file_role") or "")
    hub_kind = _core_hub_kind(str(candidate.get("path") or ""))
    covers: Set[str] = set()
    for raw in matched_terms:
        meta = _coerce_mapping(coverage_lookup.get(str(raw or "").strip().lower()))
        covers.update(str(value or "").strip() for value in _coerce_list(meta.get("covers")) if str(value or "").strip())
    aligned = bool(top_subsystems and matched_subsystems.intersection(top_subsystems))
    score = lexical + 0.85 * structural + 0.8 * graph + 0.75 * semantic
    score += min(1.35, 0.2 * len(matched_terms))
    if reason_codes.intersection({"path_hint", "symbol_match"}):
        score += 0.75
    if aligned:
        score += 0.65
        if file_role in {"implementation_code", "config_file", "test_file"}:
            score += 0.55
        elif file_role in {"documentation", "governance_doc"}:
            score += 0.15
    if covers.intersection(REQUIRED_FAILURE_COVERS):
        score += 0.4
    if reason_codes.intersection({"subsystem_structural_hint", "structural_neighbor"}):
        score += 0.45
    if _is_contamination_surface(str(candidate.get("path") or "")):
        score -= 1.8
    if hub_kind and not aligned and not reason_codes.intersection({"path_hint", "symbol_match", "subsystem_structural_hint"}):
        score -= 0.75
    if file_role in {"documentation", "governance_doc", "temporary_artifact"} and not aligned and not reason_codes.intersection({"path_hint", "symbol_match", "subsystem_structural_hint"}):
        score -= 0.35
    if lexical < 0.75 and structural < 0.45 and graph < 0.45 and semantic < 0.2 and not reason_codes.intersection({"path_hint", "symbol_match", "subsystem_structural_hint"}):
        score -= 1.2
    if len(matched_terms) <= 1 and lexical < 1.0 and not matched_subsystems.intersection(top_subsystems):
        score -= 0.45
    return round(score, 4)

def _prefilter_candidates_for_pass2(candidates: Mapping[str, Mapping[str, Any]], search_plan: Mapping[str, Any], max_candidates: int) -> Tuple[List[str], Dict[str, int]]:
    candidate_count = len(candidates)
    if candidate_count <= max(60, max_candidates * 2):
        return list(candidates.keys()), {"prefilter_candidate_count": candidate_count, "pass2_candidate_count": candidate_count}
    coverage_lookup = _query_coverage_lookup(search_plan)
    top_subsystems = {str(item or "").strip() for item in list(search_plan.get("subsystem_hypotheses") or [])[:3] if str(item or "").strip()}
    pass2_limit = min(candidate_count, max(max_candidates * 3, 80))
    pass2_limit = min(pass2_limit, 220)
    rough_ranked: List[Tuple[float, str]] = []
    must_keep: Set[str] = set()
    for path, candidate in candidates.items():
        scores = _coerce_mapping(candidate.get("scores"))
        semantic_score = float(scores.get("semantic_score") or 0.0)
        reason_codes = {str(code or "").strip() for code in _coerce_list(candidate.get("reason_codes")) if str(code or "").strip()}
        basename = Path(str(path or "")).name.lower()
        candidate_subsystems = _candidate_subsystems(candidate, coverage_lookup)
        file_role = str(candidate.get("file_role") or "")
        aligned = bool(candidate_subsystems.intersection(top_subsystems))
        if (
            reason_codes.intersection({"path_hint", "symbol_match"})
            or semantic_score >= 0.65
            or basename in MEMORY_HUB_BASENAMES
            or (aligned and file_role in {"implementation_code", "config_file", "test_file"})
            or (aligned and reason_codes.intersection({"subsystem_structural_hint", "semantic_filename_hint"}))
        ):
            must_keep.add(path)
        rough_ranked.append((_rough_candidate_prefilter_score(candidate, coverage_lookup, top_subsystems), str(path)))
    rough_ranked.sort(key=lambda item: (-item[0], item[1]))
    selected: List[str] = []
    seen: Set[str] = set()
    if len(must_keep) > pass2_limit:
        limited_must_keep = [path for _, path in rough_ranked if path in must_keep][:pass2_limit]
        must_keep = set(limited_must_keep)
    for _, path in rough_ranked:
        if path in must_keep and path not in seen:
            seen.add(path)
            selected.append(path)
    for _, path in rough_ranked:
        if len(selected) >= pass2_limit:
            break
        if path in seen:
            continue
        seen.add(path)
        selected.append(path)
    return selected, {"prefilter_candidate_count": candidate_count, "pass2_candidate_count": len(selected)}

def _semantic_prefilter_paths(
    repo_root: Path,
    search_plan: Dict[str, Any],
    task_profile: Dict[str, Any],
    deterministic_paths: List[str],
    limit: int = 180,
    ignore_matcher: Optional[_GitIgnoreMatcher] = None,
    anchor_limit: int = 60,
) -> List[str]:
    ordered_deterministic = _dedupe_strings(deterministic_paths)
    deterministic_set = set(ordered_deterministic)
    if not ordered_deterministic:
        return []
    limit = max(1, int(limit))
    anchor_limit = max(1, min(int(anchor_limit or 60), limit))
    profile = _semantic_task_profile(task_profile)
    paths: List[str] = []
    seen: Set[str] = set()

    def add(rel: str) -> None:
        r = str(rel or "").replace("\\", "/").strip()
        if not r or r in seen:
            return
        seen.add(r)
        paths.append(r)

    for rel in ordered_deterministic[:anchor_limit]:
        add(rel)

    if len(paths) >= limit:
        return paths[:limit]

    families = list(task_profile.get("task_families") or [])
    family_set = {str(item or "").strip() for item in families if str(item or "").strip()}
    if "scheduler_safety" in family_set:
        family_set.add("scheduler_hardening")
    if "reconciliation_recovery" in family_set:
        family_set.add("workflow_recovery")

    hints: List[str] = []
    if "scheduler_hardening" in family_set or "workflow_recovery" in family_set:
        hints += ["infra/bin/scheduler_", "scheduler_watchdog", "scheduler_pipeline_healer", "scheduler_recovery_assistant", "scheduler_schedule_job", "scheduler_start_job", "infra/controlplane/", "tests/control_plane/"]
    if "tool_spec_implementation" in family_set or "retrieval_ranking_contract" in family_set:
        hints += ["infra/pipeline/", "infra/bin/pipeline_", "infra/bin/go", "infra/bin/projflow", "tests/pipeline/"]
    if "deterministic_patch_helper" in family_set:
        hints += ["tools/patch_helpers/", "infra/pipeline/", "tests/"]
    if "mobile_android_ui" in family_set:
        hints += ["mobile/", "android", "app/src", "infra/webui/static/"]
    if "testing_verification" in family_set:
        hints += ["tests/", "verify", "proof"]
    if family_set.intersection(SEMANTIC_HYBRID_FAMILIES):
        hints += ["infra/webui/", "docs/contracts/", "docs/lab_assistant_", "infra/controlplane/"]
    query_tokens = _semantic_query_tokens(search_plan, task_profile)
    if family_set.intersection(CONFIG_FOCUS_FAMILIES):
        hints += ["config/", "governance/agents/prompts/", "docs/lab_assistant_", "determinism_allowlist", "runtime_policy"]
    if query_tokens.intersection(BOOTSTRAP_SIGNAL_TOKENS):
        hints += ["server_lab_assistant_bootstrap", "test_lab_assistant_bootstrap", "server_lab_assistant_task_register", "server_lab_assistant_state"]
    if query_tokens.intersection(QUEUE_SIGNAL_TOKENS):
        hints += ["server_lab_assistant_job_lifecycle", "server_lab_assistant_mailbox", "server_execution_queue", "test_lab_assistant_job_lifecycle", "test_lab_assistant_mailbox"]
    if query_tokens.intersection(RECOVERY_SIGNAL_TOKENS):
        hints += ["server_lab_assistant_recovery", "server_lab_assistant_host", "server_lab_assistant_writeback", "backend_api_boundary", "test_lab_assistant_recovery"]
    if query_tokens.intersection(MOBILE_WORKFLOW_SIGNAL_TOKENS):
        hints += ["infra/mobile/android/app/src/main/java/com/eiel/chat/", "ChatViewModel", "ChatReducer", "HttpChatApi", "ChatModels"]
    top_subsystems = {str(item or "").strip() for item in list(search_plan.get("subsystem_hypotheses") or [])[:3] if str(item or "").strip()}
    anchor_dirs = {Path(rel).parent.as_posix().lower() for rel in ordered_deterministic[: max(8, min(anchor_limit, 20))]}
    scored: List[Tuple[float, str]] = []
    for p in repo_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(repo_root).as_posix()
        if rel in seen or rel in deterministic_set:
            continue
        if any(part in EXCLUDED_DIR_PARTS for part in Path(rel).parts):
            continue
        if ignore_matcher and ignore_matcher.is_ignored(rel):
            continue
        category = _semantic_path_category(rel)
        if profile == "code" and category in {"doc", "generated_doc"} and not any(h.lower() in rel.lower() for h in hints):
            continue
        score = _score_semantic_expansion_path(
            rel,
            query_tokens=query_tokens,
            hints=hints,
            anchor_dirs=anchor_dirs,
            top_subsystems=top_subsystems,
            task_profile=task_profile,
        )
        if score < 1.05:
            continue
        scored.append((score, rel))
    scored.sort(key=lambda item: (-item[0], item[1]))
    remaining_budget = max(0, limit - len(paths))
    for _, rel in scored[:remaining_budget]:
        add(rel)
    if len(paths) < limit:
        for rel in ordered_deterministic[anchor_limit:]:
            if len(paths) >= limit:
                break
            add(rel)
    return paths[:limit]
def _load_candidate_text(abs_path: Path, text_cache: Dict[str, str]) -> str:
    cache_key = str(abs_path)
    if cache_key not in text_cache:
        try:
            text_cache[cache_key] = abs_path.read_text(encoding="utf-8", errors="ignore")[:120000].lower()
        except Exception:
            text_cache[cache_key] = ""
    return text_cache[cache_key]
def _flatten_passive_patterns(asset: Mapping[str, Any]) -> List[str]:
    patterns = _coerce_mapping(asset.get("patterns"))
    out: List[str] = []
    for values in patterns.values():
        out.extend(str(value) for value in _coerce_list(values))
    return _dedupe_strings(out)
def _enrich_candidate_coverage(
    *,
    candidate: Dict[str, Any],
    abs_path: Path,
    task_profile: Mapping[str, Any],
    intake_assessment: Mapping[str, Any],
    assets: Mapping[str, Any],
    text_cache: Dict[str, str],
    narrow_task: bool = False,
) -> None:
    path_lower = str(candidate.get("path") or "").lower()
    content = path_lower + "\n" + _load_candidate_text(abs_path, text_cache)
    cross_flags = {str(flag or "").strip() for flag in _coerce_list(task_profile.get("cross_cutting_flags")) if str(flag or "").strip()}
    family_set = {str(item or "").strip() for item in _coerce_list(task_profile.get("task_families")) if str(item or "").strip()}
    include_bias = {str(item or "").strip() for item in _coerce_list(_coerce_mapping(task_profile.get("retrieval_bias")).get("include")) if str(item or "").strip()}
    if "scheduler_safety" in family_set:
        family_set.add("scheduler_hardening")
    if "reconciliation_recovery" in family_set:
        family_set.add("workflow_recovery")
    scheduler_focus = "scheduler_hardening" in family_set or "workflow_recovery" in family_set
    recovery_focus = ("scheduler_hardening" in family_set or "workflow_recovery" in family_set) or bool(cross_flags.intersection(RECOVERY_FOCUS_FLAGS)) or bool(_coerce_mapping(intake_assessment.get("failure_taxonomy")).get("recovery_failure_modes"))
    memory_focus = bool(family_set.intersection(MEMORY_FOCUS_FAMILIES)) or bool(include_bias.intersection(MEMORY_FOCUS_SUBSYSTEMS))
    retrieval_focus = bool(family_set.intersection(RETRIEVAL_FOCUS_FAMILIES)) or bool(include_bias.intersection(RETRIEVAL_FOCUS_SUBSYSTEMS))
    config_focus = bool(family_set.intersection(CONFIG_FOCUS_FAMILIES))
    operator_control_focus = bool(family_set.intersection(OPERATOR_CONTROL_FAMILIES))
    ownership_focus = bool(cross_flags.intersection({"claim_integrity", "ownership_control", "concurrency_control"})) or ("scheduler_hardening" in family_set) or any(
        "claim" in text.lower() or "lease" in text.lower() for text in _failure_texts(intake_assessment)
    )
    operator_focus = bool(cross_flags.intersection({"operator_safety", "auditability", "validation_rigor"})) or bool(
        _coerce_mapping(intake_assessment.get("failure_taxonomy")).get("operator_failure_modes")
    )
    responsibility_catalog = _responsibility_catalog(_coerce_mapping(assets.get("responsibility_terms")))
    passive_terms = _flatten_passive_patterns(_coerce_mapping(assets.get("passive_relevance_patterns")))
    recovery_terms = _recovery_terms(_coerce_mapping(assets.get("recovery_signal_terms")))
    responsibility_hits = {
        term: _pattern_hits(content, clues)
        for term, clues in responsibility_catalog.items()
        if _pattern_hits(content, clues)
    }
    recovery_hits = _pattern_hits(content, recovery_terms)
    mutation_hits = _pattern_hits(content, sorted(MUTATION_SIGNAL_TERMS))
    ownership_hits = _pattern_hits(content, sorted(OWNERSHIP_SIGNAL_TERMS))
    validation_hits = _pattern_hits(content, sorted(VALIDATION_SIGNAL_TERMS))
    operator_hits = _pattern_hits(content, sorted(OPERATOR_SIGNAL_TERMS))
    passive_hits = _pattern_hits(content, passive_terms)
    strong_recovery_hits = _pattern_hits(content, ["run_healer", "repair_class", "detect_findings", "orphan", "stale claim", "reconcile", "reconciliation", "repair flow", "reassign", "reclaim"])
    path_recovery_owner = any(token in path_lower for token in RECOVERY_OWNERSHIP_PATH_TERMS)
    strong_path_recovery_owner = any(token in path_lower for token in ["healer", "watchdog", "recovery_assistant"])
    basename = Path(path_lower).name
    memory_path_hits = {
        cover: [token for token in tokens if token in path_lower]
        for cover, tokens in MEMORY_OWNER_PATH_TOKENS.items()
        if any(token in path_lower for token in tokens)
    }
    operator_path_hits = {
        cover: [token for token in tokens if token in path_lower]
        for cover, tokens in OPERATOR_OWNER_PATH_TOKENS.items()
        if any(token in path_lower for token in tokens)
    }
    orchestration_path_hits = {
        cover: [token for token in tokens if token in path_lower]
        for cover, tokens in ORCHESTRATION_OWNER_PATH_TOKENS.items()
        if any(token in path_lower for token in tokens)
    }
    config_path_hits = {
        cover: [token for token in tokens if token in path_lower]
        for cover, tokens in CONFIG_OWNER_PATH_TOKENS.items()
        if any(token in path_lower for token in tokens)
    }
    retrieval_path_hits = {
        cover: [token for token in tokens if token in path_lower]
        for cover, tokens in RETRIEVAL_OWNER_PATH_TOKENS.items()
        if any(token in path_lower for token in tokens)
    }
    covers: List[str] = []
    coverage_score = 0.0
    superficial_penalty = 0.0
    if recovery_focus and (recovery_hits or path_recovery_owner):
        covers.append("recovery_path")
        coverage_score += 0.55 if path_recovery_owner and not recovery_hits else 0.65 + min(0.55, 0.1 * len(recovery_hits))
    if (recovery_focus or ownership_focus) and (path_recovery_owner or strong_recovery_hits) and (mutation_hits or ownership_hits or responsibility_hits.get("recovery") or responsibility_hits.get("reconciliation") or strong_path_recovery_owner):
        covers.append("recovery_responsibility")
        coverage_score += 1.35 if strong_path_recovery_owner and not (mutation_hits or ownership_hits or responsibility_hits.get("recovery") or responsibility_hits.get("reconciliation")) else 1.75
    if ownership_focus and ownership_hits:
        covers.append("ownership_control")
        coverage_score += 0.55
    if mutation_hits or responsibility_hits.get("state_transition"):
        covers.append("state_mutation")
        coverage_score += 0.55
    if operator_focus and (operator_hits or responsibility_hits.get("operator_entrypoint")):
        covers.append("operator_flow")
        coverage_score += 0.3
    elif operator_path_hits.get("operator_flow"):
        covers.append("operator_flow")
        coverage_score += 0.82
    if operator_control_focus and (operator_path_hits.get("operator_cli") or path_lower.startswith(AGENT_PATH_PREFIX) or basename in OPERATOR_CLI_BASENAMES):
        covers.append("operator_flow")
        coverage_score += 1.35
    if validation_hits or responsibility_hits.get("validation"):
        covers.append("validation_path")
        coverage_score += 0.28
    if responsibility_hits.get("dispatch") or responsibility_hits.get("scheduling"):
        covers.append("dispatch_path")
        coverage_score += 0.2
    if memory_focus:
        if responsibility_hits.get("truth_ownership") or memory_path_hits.get("truth_boundary"):
            covers.append("truth_boundary")
            coverage_score += 1.05 if memory_path_hits.get("truth_boundary") else 0.72
        if responsibility_hits.get("reconciliation") or memory_path_hits.get("reconciliation_path"):
            covers.append("reconciliation_path")
            coverage_score += 0.95 if memory_path_hits.get("reconciliation_path") else 0.65
        if responsibility_hits.get("task_register") or memory_path_hits.get("task_register_flow"):
            covers.append("task_register_flow")
            coverage_score += 1.12 if memory_path_hits.get("task_register_flow") else 0.78
        if responsibility_hits.get("turn_transcripts") or memory_path_hits.get("transcript_flow"):
            covers.append("transcript_flow")
            coverage_score += 0.82 if memory_path_hits.get("transcript_flow") else 0.58
        if responsibility_hits.get("ledger_evidence") or responsibility_hits.get("persistence") or memory_path_hits.get("ledger_evidence"):
            covers.append("ledger_evidence")
            coverage_score += 0.9 if memory_path_hits.get("ledger_evidence") else 0.58
        if responsibility_hits.get("memory_retrieval"):
            covers.append("memory_retrieval")
            coverage_score += 0.7
        if responsibility_hits.get("writeback_projection") or memory_path_hits.get("writeback_boundary"):
            covers.append("writeback_boundary")
            coverage_score += 0.8 if memory_path_hits.get("writeback_boundary") else 0.55
        if covers and covers[-1:] and any(token in path_lower for token in ["lab_assistant", "ledger"]):
            covers.append("memory_ownership")
            coverage_score += 0.45
        if path_lower.startswith(("docs/lab_assistant_", "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_")):
            covers.append("memory_ownership")
            coverage_score += 0.85
    if family_set.intersection({"orchestration_checkpointing", "agent_orchestration_runtime", "execution_lifecycle", "lab_assistant_delegation"}):
        if orchestration_path_hits.get("recovery_responsibility"):
            covers.append("recovery_responsibility")
            coverage_score += 1.08
        if orchestration_path_hits.get("dispatch_path"):
            covers.append("dispatch_path")
            coverage_score += 0.9
        if orchestration_path_hits.get("state_mutation"):
            covers.append("state_mutation")
            coverage_score += 0.55
        if orchestration_path_hits.get("task_register_flow"):
            covers.append("task_register_flow")
            coverage_score += 0.95
        if orchestration_path_hits.get("backend_boundary"):
            covers.append("operator_flow")
            coverage_score += 0.88
    if config_focus:
        if config_path_hits.get("config_policy"):
            covers.append("validation_path")
            coverage_score += 1.0
        if config_path_hits.get("routing_policy_docs"):
            covers.append("ledger_evidence")
            coverage_score += 0.82
        if config_path_hits.get("governance_prompts"):
            covers.append("operator_flow")
            coverage_score += 0.74
    if retrieval_focus:
        retrieval_scoring_hits = _pattern_hits(content, RETRIEVAL_SCORING_SIGNAL_TERMS)
        strong_retrieval_owner = bool(
            responsibility_hits.get("retrieval_ranking")
            and (
                responsibility_hits.get("answer_assembly")
                or responsibility_hits.get("knowledge_policy")
                or validation_hits
                or mutation_hits
            )
        )
        if strong_retrieval_owner or (retrieval_path_hits.get("retrieval_owner") and (responsibility_hits.get("retrieval_ranking") or validation_hits)):
            covers.append("retrieval_owner")
            coverage_score += 1.35 if retrieval_path_hits.get("retrieval_owner") else 0.9
        if responsibility_hits.get("query_shaping") or retrieval_path_hits.get("retrieval_search"):
            covers.append("retrieval_search")
            coverage_score += 0.95 if retrieval_path_hits.get("retrieval_search") else 0.62
        if retrieval_scoring_hits and "retrieval_ranker" not in list(candidate.get("subsystems") or []):
            candidate["subsystems"] = _dedupe_strings(list(candidate.get("subsystems") or []) + ["retrieval_ranker"])
        if path_lower.endswith("infra/pipeline/stages/context_ranker.py") or retrieval_scoring_hits:
            covers.append("retrieval_scoring")
            coverage_score += 1.05 if path_lower.endswith("infra/pipeline/stages/context_ranker.py") else 0.82
        if responsibility_hits.get("answer_assembly") or retrieval_path_hits.get("retrieval_answer"):
            covers.append("retrieval_answer")
            coverage_score += 0.92 if retrieval_path_hits.get("retrieval_answer") else 0.66
        if responsibility_hits.get("knowledge_policy") or retrieval_path_hits.get("retrieval_rules"):
            covers.append("retrieval_rules")
            coverage_score += 0.58
    if any(term in basename for term in READ_ONLY_ANALYSIS_TERMS) and "recovery_responsibility" not in covers and "state_mutation" not in covers:
        superficial_penalty += 1.1
    elif passive_hits and "recovery_responsibility" not in covers and "state_mutation" not in covers:
        superficial_penalty += 0.65
    direct_memory_owner_covers = {"truth_boundary", "reconciliation_path", "task_register_flow", "transcript_flow", "ledger_evidence", "writeback_boundary"}
    if memory_focus and basename in MEMORY_HUB_BASENAMES and not set(covers).intersection(direct_memory_owner_covers):
        superficial_penalty += 1.45
    if memory_focus and path_lower.startswith("infra/pipeline/"):
        superficial_penalty += 0.95
    if retrieval_focus and basename in MEMORY_HUB_BASENAMES and not set(covers).intersection({"retrieval_owner", "retrieval_search", "retrieval_scoring", "retrieval_answer", "retrieval_rules"}):
        superficial_penalty += 1.3
    if retrieval_focus and path_lower.startswith(("infra/controlplane/", "infra/webui/")) and not set(covers).intersection({"retrieval_owner", "retrieval_search", "retrieval_scoring", "retrieval_answer"}):
        superficial_penalty += 1.1
    if memory_focus and basename == "server_routes_and_main.py" and not set(covers).intersection(direct_memory_owner_covers):
        superficial_penalty += 0.95
    if memory_focus and basename == "scheduled_jobs.py" and "scheduler_hardening" not in family_set and "workflow_recovery" not in family_set:
        superficial_penalty += 0.9
    if path_lower.startswith("tests/") and "testing_verification" not in family_set:
        test_owner_covers = {"recovery_responsibility", "truth_boundary", "reconciliation_path", "task_register_flow", "transcript_flow", "writeback_boundary", "retrieval_owner", "retrieval_search", "retrieval_scoring", "retrieval_answer"}
        if not set(covers).intersection(test_owner_covers):
            superficial_penalty += 1.15
    if rel := path_lower:
        if rel.startswith("infra/pipeline/benchmark/"):
            superficial_penalty += 6.0
        if rel.startswith(GENERATED_SCRIPT_PREFIXES) or basename in GENERATED_SCRIPT_NAMES:
            superficial_penalty += 4.8
        if any(rel.startswith(prefix) for prefix in GENERIC_DOC_NOISE_PREFIXES) and not memory_focus and not config_focus:
            superficial_penalty += 2.4
        if basename in GENERIC_TEST_NOISE_BASENAMES and "testing_verification" not in family_set and not scheduler_focus:
            superficial_penalty += 2.8
    if path_lower.startswith("mobile/") and not family_set.intersection(MOBILE_FOCUS_FAMILIES):
        superficial_penalty += 1.2
    if path_lower.startswith("infra/pipeline/") and not family_set.intersection(PIPELINE_FOCUS_FAMILIES):
        superficial_penalty += 1.15
    if basename in {"planning.py", "schemas.py"} and not retrieval_focus:
        superficial_penalty += 1.25
    if basename == "scheduled_jobs.py" and "scheduler_hardening" not in family_set and "workflow_recovery" not in family_set:
        superficial_penalty += 1.25
    if basename in SCHEDULER_TEST_BASENAMES and not scheduler_focus:
        superficial_penalty += 1.8
    if rel.startswith(SCHEDULER_BIN_PREFIXES) and not scheduler_focus:
        superficial_penalty += 2.1
    if basename in {"server_routes_and_main.py", "server_agents.py"} and family_set.intersection({"assistant_truth_and_identity", "orchestration_checkpointing"}) and not set(covers).intersection({"truth_boundary", "task_register_flow", "reconciliation_path", "dispatch_path"}):
        superficial_penalty += 0.9
    if basename in WEBUI_HUB_BASENAMES and family_set.intersection({"orchestration_checkpointing", "assistant_truth_and_identity", "execution_lifecycle"}) and not set(covers).intersection({"recovery_responsibility", "dispatch_path", "task_register_flow", "truth_boundary", "reconciliation_path"}):
        superficial_penalty += 1.25
    if basename == "server_lab_assistant_delegation_bindings.py" and "lab_assistant_delegation" not in family_set and "agent_orchestration_runtime" not in family_set:
        superficial_penalty += 2.1
    if _is_contamination_surface(path_lower):
        superficial_penalty += 1.9
    hub_kind = _core_hub_kind(path_lower)
    explicit_path_request = _candidate_has_direct_path_request(candidate, path_lower)
    has_direct_path_signal = bool(
        memory_path_hits
        or operator_path_hits
        or orchestration_path_hits
        or retrieval_path_hits
        or path_recovery_owner
        or strong_path_recovery_owner
    )
    if path_lower.startswith("tests/") and "testing_verification" not in family_set and not explicit_path_request and not has_direct_path_signal:
        superficial_penalty += 4.2
    if path_lower.startswith("mobile/") and not family_set.intersection(MOBILE_FOCUS_FAMILIES) and not explicit_path_request and not has_direct_path_signal:
        superficial_penalty += 4.0
    if path_lower.startswith("infra/pipeline/") and not family_set.intersection(PIPELINE_FOCUS_FAMILIES) and not explicit_path_request and not has_direct_path_signal:
        superficial_penalty += 4.0
    if basename in {"planning.py", "schemas.py", "scheduled_jobs.py", "server_routes_and_main.py", "server_agents.py"} and not explicit_path_request and not has_direct_path_signal:
        superficial_penalty += 3.0
    owner_cover_count = len(set(covers).intersection(OWNER_SIGNAL_COVERS))
    hub_cover_count = len(set(covers).intersection(HUB_SPAN_COVERS))
    owner_signal_strength = float(owner_cover_count)
    if strong_path_recovery_owner:
        owner_signal_strength += 1.0
    if strong_recovery_hits:
        owner_signal_strength += min(1.0, 0.25 * len(strong_recovery_hits))
    if explicit_path_request:
        owner_signal_strength += 0.45
    if responsibility_hits.get("task_register") or responsibility_hits.get("truth_ownership") or responsibility_hits.get("reconciliation"):
        owner_signal_strength += 0.35
    if hub_kind and "tool_spec_implementation" not in family_set:
        hub_penalty = {
            "scheduler_hub": 1.2,
            "webui_router_hub": 1.1,
            "webui_orchestrator_hub": 0.9,
            "pipeline_planner_hub": 1.15,
            "pipeline_stage_hub": 1.05,
            "benchmark_harness": 0.8,
        }.get(hub_kind, 0.75)
        if hub_cover_count >= 4:
            hub_penalty += 0.35
        if owner_cover_count == 0:
            hub_penalty += 0.45
        if not explicit_path_request:
            hub_penalty += 0.4
        if narrow_task:
            hub_penalty *= 1.3
        if explicit_path_request:
            hub_penalty *= 0.2
        superficial_penalty += hub_penalty
        candidate["hub_kind"] = hub_kind
        candidate["explicit_path_request"] = bool(explicit_path_request)
        candidate["hub_surface_penalty"] = round(hub_penalty, 4)
        if hub_penalty >= 0.9:
            candidate["reason_codes"].append("hub_surface_penalty")
    candidate["owner_signal_strength"] = round(owner_signal_strength, 4)
    if covers:
        candidate["covers"] = _dedupe_strings(covers)
        candidate["coverage_score"] = round(coverage_score, 4)
        candidate["scores"]["structural_score"] += min(2.6, coverage_score)
        if "recovery_responsibility" in covers:
            candidate["scores"]["graph_score"] += 0.6
            candidate["reason_codes"].append("recovery_responsibility_match")
        elif "recovery_path" in covers:
            candidate["reason_codes"].append("recovery_path_match")
        if set(covers).intersection({"truth_boundary", "reconciliation_path", "task_register_flow", "transcript_flow", "ledger_evidence", "writeback_boundary", "memory_ownership"}):
            candidate["scores"]["graph_score"] += 0.45
            candidate["reason_codes"].append("memory_owner_match")
        if set(covers).intersection({"retrieval_owner", "retrieval_scoring", "retrieval_search", "retrieval_answer", "retrieval_rules"}):
            candidate["scores"]["graph_score"] += 0.42
            candidate["reason_codes"].append("retrieval_owner_match")
    if superficial_penalty > 0:
        candidate["superficial_match_penalty"] = round(superficial_penalty, 4)
        candidate["scores"]["structural_score"] = max(0.0, float(candidate["scores"].get("structural_score") or 0.0) - min(0.85, superficial_penalty * 0.55))
        candidate["reason_codes"].append("superficial_match_penalty")


def _profile_excludes_candidate(path: str, task_profile: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    rel = str(path or "").replace("\\", "/").lower()
    families = {str(item or "").strip() for item in _coerce_list(task_profile.get("task_families")) if str(item or "").strip()}
    reason_codes = {str(code or "").strip() for code in _coerce_list(candidate.get("reason_codes")) if str(code or "").strip()}
    explicit_path_request = "path_hint" in reason_codes
    query_terms = _candidate_query_terms(candidate)
    basename = Path(rel).name
    if rel.startswith("infra/pipeline/benchmark/"):
        return True
    if rel.startswith(GENERATED_SCRIPT_PREFIXES) or Path(rel).name in GENERATED_SCRIPT_NAMES:
        return True
    if explicit_path_request:
        return False
    allow_pipeline_due_to_terms = (
        rel.startswith("tools/projflow/")
        and bool(query_terms.intersection(PROJFLOW_SIGNAL_TOKENS.union({"projflow", "planning"})))
    ) or (
        basename in PIPELINE_PLANNING_ALLOW_BASENAMES
        and bool(query_terms.intersection({"planning", "replanning", "milestone", "advance", "projflow", "portfolio"}))
    ) or (
        basename in {"search_plan.py", "planning.py"}
        and bool(families.intersection(PIPELINE_HYBRID_ALLOW_FAMILIES))
        and bool(query_terms.intersection(PIPELINE_HYBRID_SIGNAL_TOKENS))
    )
    if not families.intersection(PIPELINE_FOCUS_FAMILIES) and rel.startswith(("infra/pipeline/", "tests/pipeline/")) and not allow_pipeline_due_to_terms:
        return True
    allow_mobile_due_to_terms = bool(query_terms.intersection(MOBILE_WORKFLOW_SIGNAL_TOKENS))
    if not families.intersection(MOBILE_FOCUS_FAMILIES) and rel.startswith("mobile/") and not allow_mobile_due_to_terms:
        return True
    if rel.startswith("governance/agents/prompts/") and not (families.intersection(CONFIG_FOCUS_FAMILIES) or families.intersection(OPERATOR_CONTROL_FAMILIES)):
        return True
    scheduler_focus = (
        "scheduler_hardening" in families
        or "scheduler_safety" in families
        or (
            not families.intersection(PIPELINE_HYBRID_ALLOW_FAMILIES)
            and bool(query_terms.intersection(SCHEDULER_SIGNAL_TOKENS))
        )
    )
    if basename == "scheduled_jobs.py" and not scheduler_focus:
        return True
    if basename in SCHEDULER_TEST_BASENAMES and not scheduler_focus:
        return True
    if rel.startswith(SCHEDULER_BIN_PREFIXES) and not scheduler_focus:
        return True
    if any(rel.startswith(prefix) for prefix in GENERIC_DOC_NOISE_PREFIXES) and not (families.intersection(MEMORY_FOCUS_FAMILIES) or families.intersection(CONFIG_FOCUS_FAMILIES)):
        return True
    return False


def _apply_contextual_hub_suppression(normalized: List[Dict[str, Any]], task_profile: Mapping[str, Any]) -> None:
    family_set = {str(item or "").strip() for item in _coerce_list(task_profile.get("task_families")) if str(item or "").strip()}
    if not normalized:
        return
    orchestration_owner_present = any(
        set(_coerce_list(candidate.get("covers"))).intersection(
            {"recovery_responsibility", "task_register_flow", "dispatch_path", "truth_boundary", "reconciliation_path", "transcript_flow", "writeback_boundary"}
        )
        for candidate in normalized
    )
    config_owner_present = any(
        str(candidate.get("path") or "").lower().startswith(("config/", "governance/agents/prompts/", "docs/lab_assistant_"))
        or Path(str(candidate.get("path") or "")).name in CONFIG_OWNER_BASENAMES.union(DOC_CONFIG_OWNER_BASENAMES)
        for candidate in normalized
    )
    mobile_owner_present = any(str(candidate.get("path") or "").lower().startswith("mobile/") for candidate in normalized)
    for candidate in normalized:
        path = str(candidate.get("path") or "").replace("\\", "/").lower()
        basename = Path(path).name
        covers = set(_coerce_list(candidate.get("covers")))
        penalty = 0.0
        if family_set.intersection(CONFIG_FOCUS_FAMILIES) and config_owner_present:
            if basename in WEBUI_HUB_BASENAMES or basename == "scheduled_jobs.py" or path.startswith(SCHEDULER_BIN_PREFIXES):
                penalty += 4.6
        if family_set.intersection({"orchestration_checkpointing", "assistant_truth_and_identity"}):
            if orchestration_owner_present and not covers.intersection(ACTIVE_OWNER_COVERS):
                if basename in WEBUI_HUB_BASENAMES:
                    penalty += 3.8
                if basename == "scheduled_jobs.py":
                    penalty += 4.0
                if basename in SCHEDULER_TEST_BASENAMES:
                    penalty += 3.0
                if basename == "server_lab_assistant_delegation_bindings.py" and "dispatch_path" not in covers:
                    penalty += 2.6
                if path.startswith("infra/pipeline/"):
                    penalty += 3.4
        if "assistant_truth_and_identity" in family_set and mobile_owner_present:
            if basename in WEBUI_HUB_BASENAMES or basename == "scheduled_jobs.py":
                penalty += 2.2
        if path.startswith("docs/90_archive/markdown_source/persistent_files_chatgpt/") and orchestration_owner_present and not family_set.intersection(CONFIG_FOCUS_FAMILIES):
            penalty += 2.2
        if penalty <= 0:
            continue
        scores = _coerce_mapping(candidate.get("scores"))
        scores["final_score"] = round(max(0.0, float(scores.get("final_score") or 0.0) - penalty), 4)
        scores["retrieval_sort_score"] = round(max(0.0, float(scores.get("retrieval_sort_score") or 0.0) - penalty), 4)
        candidate["scores"] = dict(scores)
        candidate["reason_codes"] = _dedupe_strings(list(candidate.get("reason_codes") or []) + ["contextual_hub_suppression"])


def _count_searchable_repo_files(repo_root: Path, task_profile: Mapping[str, Any]) -> int:
    count = 0
    excluded_prefixes = ("jobs/", "reports/", "runtime/", "tmp/", "temp/", "out/", "snapshots/")
    for abs_path in repo_root.rglob("*"):
        if not abs_path.is_file():
            continue
        try:
            rel = abs_path.relative_to(repo_root).as_posix()
        except Exception:
            continue
        if rel.startswith(excluded_prefixes):
            continue
        if _should_exclude_candidate_path(rel, task_profile):
            continue
        count += 1
    return count


def build_retrieval(result_obj: Dict[str, Any], repo_root: Path, rg_path: str, timeout_seconds: int, config: Dict[str, Any]) -> Dict[str, Any]:
    progress = _StageProgressReporter()
    progress.update("stage 4 initializing retrieval")
    job_contract = dict(result_obj["job_contract"])
    task_profile = dict(result_obj["task_profile"])
    search_plan = dict(result_obj["search_plan"])
    intake_assessment = dict(result_obj.get("intake_assessment") or {})
    retrieval_assets = _load_retrieval_assets(job_contract.get("repo_root") or repo_root, config)
    families = list(task_profile.get("task_families") or [])
    allowed_paths = list(job_contract.get("allowed_paths") or [])
    forbidden_paths = list(job_contract.get("forbidden_paths") or [])
    negative_targets = list(search_plan.get("negative_targets") or [])
    candidates: Dict[str, Dict[str, Any]] = {}
    rg_hits_total = 0
    filename_hits_total = 0
    include_docs_for_symbol = "documentation_contracts" in families or str(task_profile.get("primary_task_type") or "").strip().lower() == "docs"
    ignore_matcher = _GitIgnoreMatcher(repo_root)
    text_cache: Dict[str, str] = {}
    def ensure_candidate(path: str) -> Optional[Dict[str, Any]]:
        rel = normalize_repo_relative_path(repo_root, path)
        if any(part in EXCLUDED_DIR_PARTS for part in Path(rel).parts):
            return None
        if ignore_matcher.is_ignored(rel):
            return None
        if _should_exclude_candidate_path(rel, task_profile):
            return None
        if _is_historical_or_generated_artifact(rel):
            return None
        if _matches_negative_target(rel, negative_targets):
            return None
        if not is_path_allowed(repo_root, rel, allowed_paths=allowed_paths, forbidden_paths=forbidden_paths):
            return None
        abs_path = repo_root / rel
        if not abs_path.exists() or not abs_path.is_file() or abs_path.stat().st_size > 1024 * 1024:
            return None
        if rel not in candidates:
            try:
                sample = abs_path.read_text(encoding="utf-8", errors="ignore")[:2048]
            except Exception:
                sample = ""
            file_role = classify_file_role(rel, sample)
            candidates[rel] = {
                "path": rel,
                "language": abs_path.suffix.lstrip(".") or None,
                "exists": True,
                "file_role": file_role,
                "candidate_role": compatible_candidate_role(file_role),
                "task_role_fit": 0.0,
                "reason_codes": [],
                "scores": {"lexical_score": 0.0, "structural_score": 0.0, "graph_score": 0.0, "recency_score": 0.0, "semantic_score": 0.0, "role_fit_score": 0.0, "final_score": 0.0, "retrieval_sort_score": 0.0},
                "matched_terms": [],
                "matched_symbols": [],
                "line_hits": [],
                "semantic_evidence": [],
                "evidence_sources": [],
            }
        return candidates[rel]
    query_entries = [
        _coerce_mapping(query)
        for query in list(search_plan.get("queries") or [])
        if isinstance(query, Mapping)
    ]
    query_meta_by_text: Dict[str, Dict[str, Any]] = {}
    grouped_queries: Dict[str, List[str]] = {"literal": [], "test_name": [], "symbol": [], "filename": []}
    for query in query_entries:
        q = str(query.get("query") or "").strip()
        qtype = str(query.get("query_type") or "literal")
        weight = max(0.15, min(2.0, float(query.get("weight") or 1.0)))
        if not q:
            continue
        query_meta_by_text[q] = {"query_type": qtype, "weight": weight}
        grouped_queries.setdefault(qtype, []).append(q)
    rg_batches_total = 0
    progress.update(
        f"stage 4 deterministic search: 0/{sum(len(grouped_queries.get(qtype) or []) for qtype in ['literal', 'test_name', 'symbol'])} queries",
        phase="deterministic_search",
    )
    for qtype in ["literal", "test_name", "symbol"]:
        queries = grouped_queries.get(qtype) or []
        progress.update(
            f"stage 4 scanning {qtype} queries ({len(queries)} terms)",
            phase="deterministic_search",
            query_type=qtype,
            query_count=len(queries),
        )
        hits, batch_count = _collect_rg_hits_batched(
            repo_root,
            rg_path,
            queries,
            qtype,
            timeout_seconds,
            include_docs_for_symbol=include_docs_for_symbol if qtype == "symbol" else False,
        )
        rg_batches_total += batch_count
        for hit in hits:
            query_text = str(hit.get("query") or "").strip()
            meta = _coerce_mapping(query_meta_by_text.get(query_text))
            weight = max(0.15, min(2.0, float(meta.get("weight") or 1.0)))
            candidate = ensure_candidate(str(hit.get("path") or ""))
            if candidate is None:
                continue
            rg_hits_total += 1
            candidate["reason_codes"].append("keyword_match" if qtype != "symbol" else "symbol_match")
            term_seen = query_text in list(candidate.get("matched_terms") or [])
            symbol_seen = query_text in list(candidate.get("matched_symbols") or [])
            candidate["matched_terms"].append(query_text)
            if qtype == "symbol":
                candidate["matched_symbols"].append(query_text)
                if not symbol_seen:
                    candidate["scores"]["structural_score"] += 1.2 * weight
            else:
                if not term_seen:
                    candidate["scores"]["lexical_score"] += 1.0 * weight
            candidate["line_hits"].append({"start_line": int(hit.get("line") or 1), "end_line": int(hit.get("line") or 1), "preview": str(hit.get("preview") or "")[:300]})
            if "deterministic" not in candidate["evidence_sources"]:
                candidate["evidence_sources"].append("deterministic")
    explicit_file_hints = _dedupe_strings(list(result_obj.get("extracted_terms", {}).get("file_hints", []) or []))
    progress.update("stage 4 filename scan", phase="filename_scan")
    filename_hint_map, filename_scans_total = _collect_filename_hits_by_query(
        repo_root,
        list(grouped_queries.get("filename") or []) + explicit_file_hints,
        ignore_matcher=ignore_matcher,
    )
    for query_text in grouped_queries.get("filename") or []:
        meta = _coerce_mapping(query_meta_by_text.get(query_text))
        weight = max(0.15, min(2.0, float(meta.get("weight") or 1.0)))
        for rel in filename_hint_map.get(query_text, []):
            candidate = ensure_candidate(rel)
            if candidate is None:
                continue
            filename_hits_total += 1
            candidate["reason_codes"].append("filename_match")
            if query_text not in list(candidate.get("matched_terms") or []):
                candidate["matched_terms"].append(query_text)
                candidate["scores"]["graph_score"] += 0.9 * weight
            candidate["line_hits"].append({"start_line": 1, "end_line": 1, "preview": f"filename match: {Path(rel).name}"})
            if "deterministic" not in candidate["evidence_sources"]:
                candidate["evidence_sources"].append("deterministic")
    for query_text in explicit_file_hints:
        for rel in filename_hint_map.get(query_text, []):
            candidate = ensure_candidate(rel)
            if candidate is None:
                continue
            filename_hits_total += 1
            candidate["reason_codes"].append("path_hint")
            candidate["scores"]["graph_score"] += 0.8
            if "deterministic" not in candidate["evidence_sources"]:
                candidate["evidence_sources"].append("deterministic")
    semantic_filename_hints = _semantic_filename_hints(search_plan, task_profile)
    progress.update("stage 4 semantic filename scan", phase="filename_scan")
    semantic_filename_map, semantic_filename_scans = _collect_filename_hits_by_query(
        repo_root,
        semantic_filename_hints,
        ignore_matcher=ignore_matcher,
    )
    filename_scans_total += semantic_filename_scans
    for query_text in semantic_filename_hints:
        for rel in semantic_filename_map.get(query_text, []):
            candidate = ensure_candidate(rel)
            if candidate is None:
                continue
            filename_hits_total += 1
            candidate["reason_codes"].append("semantic_filename_hint")
            if query_text not in list(candidate.get("matched_terms") or []):
                candidate["matched_terms"].append(query_text)
            candidate["scores"]["graph_score"] += 1.15
            candidate["line_hits"].append({"start_line": 1, "end_line": 1, "preview": f"semantic filename hint: {Path(rel).name}"})
            if "deterministic" not in candidate["evidence_sources"]:
                candidate["evidence_sources"].append("deterministic")
    structural_hint_matches = 0
    subsystem_structural_hints = _stage4_subsystem_structural_hints(
        search_plan,
        task_profile,
        retrieval_assets.get("subsystem_map") or {},
        limit=int((config.get("retrieval_policy") or {}).get("stage4_structural_hint_limit") or 24),
    )
    if subsystem_structural_hints:
        progress.update("stage 4 subsystem structural scan", phase="filename_scan")
        subsystem_filename_map, subsystem_filename_scans = _collect_filename_hits_by_query(
            repo_root,
            subsystem_structural_hints,
            ignore_matcher=ignore_matcher,
        )
        filename_scans_total += subsystem_filename_scans
        for query_text in subsystem_structural_hints:
            for rel in subsystem_filename_map.get(query_text, []):
                candidate = ensure_candidate(rel)
                if candidate is None:
                    continue
                structural_hint_matches += 1
                filename_hits_total += 1
                candidate["reason_codes"].append("subsystem_structural_hint")
                if query_text not in list(candidate.get("matched_terms") or []):
                    candidate["matched_terms"].append(query_text)
                candidate["scores"]["structural_score"] += 0.55
                candidate["scores"]["graph_score"] += 0.4
                candidate["line_hits"].append({"start_line": 1, "end_line": 1, "preview": f"subsystem structural hint: {Path(rel).name}"})
                if "deterministic" not in candidate["evidence_sources"]:
                    candidate["evidence_sources"].append("deterministic")
    memory_focus = bool(set(families).intersection(MEMORY_FOCUS_FAMILIES)) or bool(set(_coerce_list(_coerce_mapping(task_profile.get("retrieval_bias")).get("include"))).intersection(MEMORY_FOCUS_SUBSYSTEMS))
    if memory_focus:
        progress.update("stage 4 memory hub scan", phase="memory_hub_scan")
        memory_hub_hits, hub_scan_count = _collect_filename_hits_by_query(repo_root, sorted(MEMORY_CONTEXT_SCAN_BASENAMES), ignore_matcher=ignore_matcher)
        filename_scans_total += hub_scan_count
        for basename in sorted(MEMORY_CONTEXT_SCAN_BASENAMES):
            for rel in memory_hub_hits.get(basename, []):
                candidate = ensure_candidate(rel)
                if candidate is None:
                    continue
                candidate["reason_codes"].append("hub_context")
                candidate["scores"]["graph_score"] += 0.35
                if "deterministic" not in candidate["evidence_sources"]:
                    candidate["evidence_sources"].append("deterministic")
    deterministic_candidate_paths = list(candidates.keys())
    ordered_deterministic_candidate_paths = _ordered_deterministic_paths_for_semantic(candidates, search_plan)
    semantic_runtime = semantic_runtime_config(config)
    semantic_meta: Dict[str, Any] = dict(semantic_runtime)
    semantic_results: List[Dict[str, Any]] = []
    candidate_paths: List[str] = list(ordered_deterministic_candidate_paths)
    query_text = str(search_plan.get("semantic_query_text") or "").strip() or " ".join(q.get("query", "") for q in list(search_plan.get("queries") or [])[:6]).strip()
    effective_mode = str(semantic_runtime.get("effective_mode") or "off")
    if effective_mode in {"retrieve_and_rank", "rank_only"} and query_text:
        semantic_cfg = dict(config.get("semantic") or {})
        if effective_mode == "retrieve_and_rank":
            progress.update("stage 4 semantic prefilter", phase="semantic_prefilter")
            candidate_paths = _semantic_prefilter_paths(
                repo_root,
                search_plan,
                task_profile,
                ordered_deterministic_candidate_paths,
                limit=int(semantic_cfg.get("semantic_prefilter_limit") or 180),
                ignore_matcher=ignore_matcher,
                anchor_limit=int(semantic_cfg.get("semantic_prefilter_anchor_limit") or 60),
            )
        else:
            rank_only_limit = int(semantic_cfg.get("semantic_rank_only_candidate_limit") or 40)
            candidate_paths = ordered_deterministic_candidate_paths[:rank_only_limit]
        progress.update(
            f"stage 4 semantic retrieval ({len(candidate_paths)} candidates)",
            phase="semantic_retrieval",
            candidate_count=len(candidate_paths),
            semantic_mode=effective_mode,
        )
        semantic_results, semantic_meta = semantic_retrieve(
            repo_root=repo_root,
            query_text=query_text,
            config=config,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            candidate_paths=candidate_paths,
        )
        evidence_cap = max(1, int(semantic_cfg.get("semantic_evidence_per_file") or 2))
        semantic_weight = max(0.0, float(semantic_cfg.get("semantic_score_weight") or 0.35))
        lexical_bonus_cap = max(0.0, float(semantic_cfg.get("semantic_lexical_bonus_cap") or 2.5))
        semantic_candidate_cap = int(semantic_cfg.get("semantic_candidate_cap") or 32)
        for item in semantic_results[:semantic_candidate_cap]:
            candidate = ensure_candidate(str(item.get("path") or ""))
            if candidate is None:
                continue
            raw_score = item.get("semantic_score")
            if raw_score is None:
                raw_score = item.get("score")
            raw_score_value = float(raw_score or 0.0)
            semantic_prior = _semantic_path_prior(str(candidate.get("path") or item.get("path") or ""), task_profile)
            adjusted_score = round(raw_score_value * semantic_prior, 4)
            item["semantic_score_raw"] = round(raw_score_value, 4)
            item["semantic_score_adjusted"] = adjusted_score
            item["semantic_path_category"] = _semantic_path_category(str(item.get("path") or ""))
            item["semantic_path_prior"] = round(semantic_prior, 4)
            score = adjusted_score
            candidate["scores"]["semantic_score"] = max(float(candidate["scores"].get("semantic_score") or 0.0), score)
            lexical_bonus = min(score * 0.35, lexical_bonus_cap)
            candidate["scores"]["lexical_score"] += lexical_bonus
            if "semantic" not in candidate["evidence_sources"]:
                candidate["evidence_sources"].append("semantic")
            candidate["reason_codes"].append("semantic_match")
            evidence_items = list(item.get("evidence") or [])
            if not evidence_items:
                preview = str(item.get("chunk_preview") or "").strip()
                if preview:
                    evidence_items = [
                        {
                            "line": int(item.get("line") or 1),
                            "preview": preview,
                            "score": score,
                        }
                    ]
            evidence_items = evidence_items[:evidence_cap]
            for ev in evidence_items:
                candidate["semantic_evidence"].append(
                    {
                        "line": int(ev.get("line") or 1),
                        "preview": str(ev.get("preview") or "")[:300],
                        "score": round(float(ev.get("score") or score), 4),
                    }
                )
                if not candidate["line_hits"]:
                    candidate["line_hits"].append({"start_line": int(ev.get("line") or 1), "end_line": int(ev.get("line") or 1), "preview": str(ev.get("preview") or "")[:300]})
    max_candidates = int(search_plan.get("max_candidates") or 100)
    pass2_paths, prefilter_stats = _prefilter_candidates_for_pass2(candidates, search_plan, max_candidates)
    normalized: List[Dict[str, Any]] = []
    normalized_paths: Set[str] = set()
    semantic_weight = max(0.0, float(config.get("semantic", {}).get("semantic_score_weight") or 0.35))
    coverage_lookup = _query_coverage_lookup(search_plan)
    active_subsystems = _dedupe_strings(list(search_plan.get("subsystem_hypotheses") or []))
    narrow_task_flag = "narrow_task_suppression=true" in list(search_plan.get("notes") or [])

    def _normalize_candidate(rel: str) -> bool:
        candidate = candidates.get(rel)
        if candidate is None or rel in normalized_paths:
            return False
        _enrich_candidate_coverage(
            candidate=candidate,
            abs_path=repo_root / rel,
            task_profile=task_profile,
            intake_assessment=intake_assessment,
            assets=retrieval_assets,
            text_cache=text_cache,
            narrow_task=narrow_task_flag,
        )
        _apply_semantic_owner_boost(candidate, task_profile=task_profile, search_plan=search_plan)
        if not list(candidate.get("subsystems") or []):
            candidate["subsystems"] = sorted(_candidate_subsystems(candidate, coverage_lookup))
        candidate["active_subsystems"] = list(active_subsystems)
        annotate_stage4_candidate(candidate, narrow_task=narrow_task_flag)
        if _profile_excludes_candidate(rel, task_profile, candidate):
            return False
        scores = candidate["scores"]
        file_role = str(candidate.get("file_role") or classify_file_role(rel, ""))
        task_archetype = str(task_profile.get("task_archetype") or "implementation_change")
        role_fit_score = max(0.0, file_role_weight(task_archetype, file_role, "retrieval") - 0.75)
        candidate["task_role_fit"] = round(role_fit_score, 4)
        scores["lexical_score"] = round(float(scores["lexical_score"]), 4)
        scores["structural_score"] = round(float(scores["structural_score"]), 4)
        scores["graph_score"] = round(float(scores["graph_score"]), 4)
        scores["semantic_score"] = round(float(scores["semantic_score"]), 4)
        scores["role_fit_score"] = round(float(role_fit_score), 4)
        scores["final_score"] = round(
            float(scores["lexical_score"])
            + float(scores["structural_score"])
            + float(scores["graph_score"])
            + float(scores["recency_score"])
            + float(scores["semantic_score"])
            + float(scores["role_fit_score"]),
            4,
        )
        if str(semantic_meta.get("effective_mode") or "off") in {"retrieve_and_rank", "rank_only"}:
            scores["retrieval_sort_score"] = round(scores["final_score"] + semantic_weight * float(scores.get("semantic_score") or 0.0), 4)
        else:
            scores["retrieval_sort_score"] = scores["final_score"]
        normalized.append(ensure_retrieval_candidate_foundation(candidate))
        normalized_paths.add(rel)
        return True

    total_pass2 = len(pass2_paths)
    progress.update(f"stage 4 ranking candidates (0/{total_pass2})", phase="candidate_enrichment", total=total_pass2)
    for idx, rel in enumerate(pass2_paths, start=1):
        if idx == 1 or idx == total_pass2 or idx % 25 == 0:
            progress.update(
                f"stage 4 ranking candidates ({idx}/{total_pass2})",
                phase="candidate_enrichment",
                current=idx,
                total=total_pass2,
            )
        _normalize_candidate(rel)

    structural_neighbor_limit = int((config.get("retrieval_policy") or {}).get("stage4_structural_neighbor_limit") or 16)
    structural_neighbor_paths = _expand_structural_neighbor_paths(
        repo_root,
        normalized,
        ignore_matcher=ignore_matcher,
        limit=structural_neighbor_limit,
    )
    expanded_neighbor_count = 0
    for rel in structural_neighbor_paths:
        candidate = ensure_candidate(rel)
        if candidate is None:
            continue
        candidate["reason_codes"].append("structural_neighbor")
        candidate["scores"]["structural_score"] += 0.65
        candidate["scores"]["graph_score"] += 0.25
        candidate["line_hits"].append({"start_line": 1, "end_line": 1, "preview": f"structural neighbor: {Path(rel).name}"})
        if "deterministic" not in candidate["evidence_sources"]:
            candidate["evidence_sources"].append("deterministic")
        if _normalize_candidate(rel):
            expanded_neighbor_count += 1

    normalized.sort(key=lambda item: (-float(item["scores"].get("retrieval_sort_score", item["scores"]["final_score"])), -float(item["scores"]["final_score"]), item["path"]))
    normalized = normalized[:max_candidates]

    if not normalized:
        explicit_hint_paths: List[str] = []
        for value in list(allowed_paths) + list(_coerce_list(_coerce_mapping(job_contract.get("issue_context") or {}).get("changed_files_hint"))) + list(_coerce_list(_coerce_mapping(search_plan.get("extracted_terms") or {}).get("file_hints"))):
            raw_hint = str(value or "").strip().rstrip(".,:;")
            if not raw_hint:
                continue
            try:
                explicit_hint_paths.append(normalize_repo_relative_path(repo_root, raw_hint))
            except Exception:
                explicit_hint_paths.append(raw_hint.replace("\\", "/").strip("/"))
        explicit_hint_paths = list(dict.fromkeys(explicit_hint_paths))
        for rel in explicit_hint_paths:
            candidate = ensure_candidate(rel)
            if candidate is None:
                continue
            candidate["reason_codes"].append("explicit_scope_hint")
            candidate["scores"]["structural_score"] += 0.5
            candidate["scores"]["graph_score"] += 0.15
            candidate["line_hits"].append({"start_line": 1, "end_line": 1, "preview": f"explicit scope hint: {Path(rel).name}"})
            if "deterministic" not in candidate["evidence_sources"]:
                candidate["evidence_sources"].append("deterministic")
            _normalize_candidate(rel)
        normalized.sort(key=lambda item: (-float(item["scores"].get("retrieval_sort_score", item["scores"]["final_score"])), -float(item["scores"]["final_score"]), item["path"]))
        normalized = normalized[:max_candidates]

    progress.update(f"stage 4 finalizing retrieval ({len(normalized)} selected)", phase="finalize", selected_count=len(normalized))
    if not normalized:
        searchable_file_count = _count_searchable_repo_files(repo_root, task_profile)
        if searchable_file_count == 0:
            raise PipelineError(
                "no_searchable_repo_files",
                "Target repo has no searchable source files after exclusions",
                details={
                    "repo_root": str(repo_root),
                    "searchable_file_count": 0,
                    "hint": "Use --repo-root to point at the actual code repository, or add source files such as app.py and tests before running the pipeline.",
                },
                exit_code=5,
            )
        raise PipelineError(
            "no_useful_result",
            "Zero candidates after search",
            details={
                "repo_root": str(repo_root),
                "searchable_file_count": searchable_file_count,
                "hint": "The repo contains files, but none matched the Stage 3 search plan. Check the target repo path and prompt specificity.",
            },
            exit_code=5,
        )
    deterministic_path_set = set(ordered_deterministic_candidate_paths)
    semantic_expansion_paths = sorted([path for path in candidate_paths if path not in deterministic_path_set])
    semantic_only_paths = sorted([str(item["path"]) for item in normalized if "semantic" in list(item.get("evidence_sources") or []) and "deterministic" not in list(item.get("evidence_sources") or [])])
    semantic_result_category_counts: Dict[str, int] = {}
    semantic_result_file_role_counts: Dict[str, int] = {}
    for item in semantic_results:
        path = str(item.get("path") or "")
        category = _semantic_path_category(path)
        semantic_result_category_counts[category] = int(semantic_result_category_counts.get(category, 0)) + 1
        role = classify_file_role(path, str(item.get("chunk_preview") or ""))
        semantic_result_file_role_counts[role] = int(semantic_result_file_role_counts.get(role, 0)) + 1
    retrieval_candidate_file_role_counts: Dict[str, int] = {}
    for item in normalized:
        role = str(item.get("file_role") or "unknown")
        retrieval_candidate_file_role_counts[role] = int(retrieval_candidate_file_role_counts.get(role, 0)) + 1
    retrieval_strategy = str(semantic_meta.get("effective_strategy") or "deterministic_only")
    return {
        "intake_assessment": ensure_intake_assessment(intake_assessment),
        "job_contract": job_contract,
        "task_profile": ensure_task_profile_foundation(task_profile),
        "search_plan": ensure_search_plan_foundation(search_plan),
        "extracted_terms": _coerce_mapping(result_obj.get("extracted_terms")),
        "retrieval": {
            "file_candidates": normalized,
            "retrieval_stats": {
                "files_scanned": int(prefilter_stats.get("pass2_candidate_count", len(normalized))),
                "rg_hits": rg_hits_total,
                "rg_batches": rg_batches_total,
                "ast_hits": 0,
                "filename_hits": filename_hits_total,
                "filename_scans": filename_scans_total,
                "expanded_neighbors": expanded_neighbor_count,
                "subsystem_structural_hint_matches": structural_hint_matches,
                "deduplicated_count": len(normalized),
                "prefilter_candidate_count": int(prefilter_stats.get("prefilter_candidate_count", len(candidates))),
                "pass2_candidate_count": int(prefilter_stats.get("pass2_candidate_count", len(normalized))),
                "semantic_status": semantic_meta.get("semantic_status"),
                "semantic_mode_requested": semantic_meta.get("requested_mode"),
                "semantic_mode_effective": semantic_meta.get("effective_mode"),
                "semantic_returned": semantic_meta.get("semantic_returned", 0),
                "semantic_prefilter_count": len(candidate_paths),
                "semantic_prefilter_anchor_limit": int(config.get("semantic", {}).get("semantic_prefilter_anchor_limit") or 60),
                "semantic_rank_only_candidate_limit": int(config.get("semantic", {}).get("semantic_rank_only_candidate_limit") or 40),
                "semantic_expansion_candidate_count": len(semantic_expansion_paths),
                "semantic_expansion_candidate_paths": semantic_expansion_paths[:25],
                "semantic_only_candidate_count": len(semantic_only_paths),
                "semantic_result_category_counts": semantic_result_category_counts,
                "semantic_result_file_role_counts": semantic_result_file_role_counts,
                "retrieval_candidate_file_role_counts": retrieval_candidate_file_role_counts,
                "retrieval_strategy": retrieval_strategy,
                "gitignore_respected": True,
                "negative_targets_applied": list(negative_targets),
            },
            "semantic": {
                "status": semantic_meta.get("semantic_status"),
                "mode": semantic_meta.get("effective_mode"),
                "runtime": {
                    **semantic_meta,
                    "query_text": query_text[:1200],
                    "semantic_prefilter_count": len(candidate_paths),
                    "semantic_prefilter_anchor_limit": int(config.get("semantic", {}).get("semantic_prefilter_anchor_limit") or 60),
                    "semantic_rank_only_candidate_limit": int(config.get("semantic", {}).get("semantic_rank_only_candidate_limit") or 40),
                    "semantic_expansion_candidate_count": len(semantic_expansion_paths),
                    "semantic_expansion_candidate_paths": semantic_expansion_paths[:25],
                    "semantic_only_candidate_count": len(semantic_only_paths),
                    "semantic_only_candidate_paths": semantic_only_paths,
                    "semantic_result_category_counts": semantic_result_category_counts,
                    "semantic_result_file_role_counts": semantic_result_file_role_counts,
                    "retrieval_candidate_file_role_counts": retrieval_candidate_file_role_counts,
                    "deterministic_candidate_count": len(deterministic_candidate_paths),
                    "ordered_deterministic_candidate_count": len(ordered_deterministic_candidate_paths),
                    "final_candidate_count": len(normalized),
                },
                "requested": {
                    "enabled": semantic_meta.get("requested_enabled"),
                    "mode": semantic_meta.get("requested_mode"),
                    "provider": semantic_meta.get("requested_provider"),
                    "model": semantic_meta.get("requested_model"),
                },
                "effective": {
                    "enabled": semantic_meta.get("effective_enabled"),
                    "mode": semantic_meta.get("effective_mode"),
                    "provider": semantic_meta.get("effective_provider"),
                    "model": semantic_meta.get("effective_model"),
                    "strategy": semantic_meta.get("effective_strategy"),
                },
                "results": semantic_results,
            },
            "retrieval_provenance": {
                "strategy": retrieval_strategy,
                "semantic_status": semantic_meta.get("semantic_status"),
                "requested_mode": semantic_meta.get("requested_mode"),
                "effective_mode": semantic_meta.get("effective_mode"),
                "requested_provider": semantic_meta.get("requested_provider"),
                "effective_provider": semantic_meta.get("effective_provider"),
                "requested_model": semantic_meta.get("requested_model"),
                "effective_model": semantic_meta.get("effective_model"),
                "disabled_reason": semantic_meta.get("disabled_reason"),
                "semantic_only_candidate_count": len(semantic_only_paths),
                "semantic_only_candidate_paths": semantic_only_paths,
                "semantic_expansion_candidate_count": len(semantic_expansion_paths),
                "semantic_expansion_candidate_paths": semantic_expansion_paths[:25],
                "semantic_result_category_counts": semantic_result_category_counts,
                "semantic_result_file_role_counts": semantic_result_file_role_counts,
                "retrieval_candidate_file_role_counts": retrieval_candidate_file_role_counts,
            },
        },
    }
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MVP-1 deterministic repo retriever")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        payload = load_json(args.input)
        env = validate_stage_input(payload, ["job_contract", "task_profile", "search_plan"])
        repo_root = Path(str(env.result["job_contract"].get("repo_root") or args.repo_root)).resolve()
        input_result = dict(env.result)
        retry_contract = dict(input_result.get("retry_contract") or {})
        if retry_contract:
            search_plan = dict(input_result.get("search_plan") or {})
            lexical_queries = _dedupe_strings(list(search_plan.get("lexical_queries") or []))
            for path in list(dict(retry_contract.get("feedback_bundle") or {}).get("affected_files") or [])[:4]:
                basename = Path(str(path or "")).name.strip()
                if basename:
                    lexical_queries.append(basename)
            lexical_queries.extend(list(dict(retry_contract.get("failure_signature") or {}).get("key_error_tokens") or [])[:6])
            search_plan["lexical_queries"] = _dedupe_strings(lexical_queries)
            search_plan["notes"] = _dedupe_strings(list(search_plan.get("notes") or []) + ["retry_contract_expanded_retrieval=true"])
            input_result["search_plan"] = search_plan
        result = build_retrieval(input_result, repo_root, str(config.get("tools", {}).get("rg_path", "rg")), int(config.get("timeouts", {}).get("search_seconds", 120)), config)
        if retry_contract:
            result["retry_contract"] = retry_contract
        envelope = build_envelope(stage=STAGE_KEY, job_id=str(env.job_id), run_id=str(env.run_id), input_payload=payload, result=result, tool_version=str(config.get("pipeline", {}).get("tool_version")), started_at_iso=started)
        save_json_atomic(args.output, envelope)
        return 0
    except Exception as exc:
        return emit_pipeline_error(exc)
if __name__ == "__main__":
    raise SystemExit(main())
