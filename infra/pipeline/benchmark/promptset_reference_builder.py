from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import yaml


COMMON_NEGATIVE_TARGETS = [
    "tests/benchmarks/pipeline/**",
    "runtime/**",
    "tmp/**",
    "*.log",
    "*.trace",
    "*.dump",
    "*.tmp",
    "*.cache",
]

PIPELINE_BOOTSTRAP_DOC = "docs/90_archive/markdown_source/agentic-pipeline-v0.1/00-Bootstrap.md"
LAB_AGENT_BOOTSTRAP_DOC = "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_agent_bootstrap.md"


PROFILE_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "scheduler": {
        "goal": "Align the prompt to scheduler truth, dispatch safety, and server-resident operational control rather than generic chat behavior.",
        "in_scope": [
            "scheduler state and health",
            "job dispatch or scheduling policy",
            "operator-visible execution state",
            "recovery and duplicate suppression",
        ],
        "out_of_scope": [
            "resume or recruiting-domain content quality",
            "UI-only polish",
            "generic LLM persona shaping",
        ],
        "stage1": {
            "expected_families": ["scheduler_safety", "scheduler_or_control_plane"],
            "accepted_families": ["orchestration_checkpointing", "api_control_plane", "worker_runtime_safety"],
            "forbidden_families": ["retrieval_ranking_contract"],
        },
        "stage2": {
            "expected_primary": "system_hardening",
            "partial_primaries": ["orchestration_checkpointing", "feature", "code_modification"],
            "forbidden_primaries": ["retrieval_ranking_contract"],
        },
        "stage3": {
            "expected_subsystems": ["scheduler_core", "lease_claims", "schedule_calculation"],
            "secondary_subsystems": ["recovery_mechanisms", "observability", "validation"],
            "forbidden_subsystems": ["retrieval_ranker"],
        },
        "stage7": {
            "implementation_targets": [
                "infra/controlplane/scheduled_jobs.py",
                "infra/controlplane/job_registry.py",
                "infra/controlplane/server_maintenance.py",
            ],
            "target_patterns": [
                "infra/controlplane/*.py",
                "tests/control_plane/test_schedule*.py",
                "tests/control_plane/test_scheduler_*.py",
                "tests/control_plane/test_scheduled_jobs.py",
            ],
            "reference_context": [
                "infra/webui/server_execution_queue.py",
                "infra/webui/server_lab_assistant_job_lifecycle.py",
            ],
            "reference_context_patterns": [
                "infra/webui/server_lab_assistant_*.py",
                "tests/control_plane/webui/test_webui_schedules.py",
            ],
            "forbidden_patterns": [
                "**/benchmark-gpt/**",
                "infra/pipeline/knowledge/**",
            ],
        },
        "domains": ["scheduler control", "dispatch safety", "operational recovery"],
        "failure_modes": ["duplicate launches", "stale claims", "silent scheduler failure"],
        "invariants": ["scheduler-owned truth", "bounded repair", "no duplicate start of one logical job"],
    },
    "checkpoint": {
        "goal": "Map the prompt to checkpointing, restart safety, and crash-consistent lifecycle persistence.",
        "in_scope": [
            "crash-safe progress markers",
            "restart or replay handling",
            "durable lifecycle state",
            "idempotent recovery",
        ],
        "out_of_scope": [
            "feature UX details",
            "content generation policy",
            "unrelated UI shell changes",
        ],
        "stage1": {
            "expected_families": ["orchestration_checkpointing", "execution_lifecycle"],
            "accepted_families": ["scheduler_safety", "worker_runtime_safety", "stateful_workflow"],
            "forbidden_families": ["retrieval_ranking_contract"],
        },
        "stage2": {
            "expected_primary": "orchestration_checkpointing",
            "partial_primaries": ["system_hardening", "code_modification"],
            "forbidden_primaries": ["retrieval_ranking_contract"],
        },
        "stage3": {
            "expected_subsystems": ["checkpoint_resume", "recovery_mechanisms", "state_persistence"],
            "secondary_subsystems": ["heartbeat_liveness", "validation", "worker_runtime"],
            "forbidden_subsystems": ["retrieval_ranker"],
        },
        "stage7": {
            "implementation_targets": [
                "infra/controlplane/scheduled_jobs.py",
                "infra/webui/server_lab_assistant_recovery.py",
                "infra/webui/server_lab_assistant_state.py",
            ],
            "target_patterns": [
                "infra/controlplane/*.py",
                "infra/webui/server_lab_assistant_recovery.py",
                "infra/webui/server_lab_assistant_state.py",
                "tests/control_plane/test_scheduler_*.py",
                "tests/control_plane/webui/test_lab_assistant_recovery.py",
            ],
            "reference_context": [
                "infra/webui/server_lab_assistant_turns.py",
                "infra/webui/server_lab_assistant_execution_reconciliation.py",
            ],
            "reference_context_patterns": [
                "tests/control_plane/webui/test_lab_assistant_*.py",
            ],
            "forbidden_patterns": [
                "**/benchmark-gpt/**",
                "infra/pipeline/knowledge/**",
            ],
        },
        "domains": ["checkpointing", "recovery semantics", "durable execution state"],
        "failure_modes": ["replay after restart", "stuck running state", "missing progress marker"],
        "invariants": ["persist before side effects", "fail-closed replay", "append-only recovery evidence"],
    },
    "truth": {
        "goal": "Interpret the prompt as truth ownership, durable state precedence, and reconciliation across runtime-visible surfaces.",
        "in_scope": [
            "source-of-truth precedence",
            "durable assistant or workflow state",
            "reconciliation and evidence trails",
            "append-only or auditable mutation paths",
        ],
        "out_of_scope": [
            "content style quality",
            "frontend-only presentation",
            "generic answer-writing",
        ],
        "stage1": {
            "expected_families": ["assistant_truth_and_identity", "persistence_integrity"],
            "accepted_families": ["lab_assistant_memory", "stateful_workflow", "generic"],
            "forbidden_families": ["retrieval_ranking_contract"],
        },
        "stage2": {
            "expected_primary": "truth_contract",
            "partial_primaries": ["code_modification", "feature", "design"],
            "forbidden_primaries": ["retrieval_ranking_contract"],
        },
        "stage3": {
            "expected_subsystems": ["truth_ownership", "state_persistence", "execution_reconciliation"],
            "secondary_subsystems": ["turn_transcripts", "task_register", "validation"],
            "forbidden_subsystems": ["scheduler_core"],
        },
        "stage7": {
            "implementation_targets": [
                "infra/webui/server_lab_assistant_state.py",
                "infra/webui/server_lab_assistant_execution_truth_ownership.py",
                "infra/webui/server_lab_assistant_execution_reconciliation.py",
            ],
            "target_patterns": [
                "infra/webui/server_lab_assistant_state.py",
                "infra/webui/server_lab_assistant_execution_*.py",
                "infra/webui/server_lab_assistant_writeback.py",
                "tests/control_plane/webui/test_lab_assistant_state.py",
                "tests/control_plane/webui/test_lab_assistant_execution_*.py",
            ],
            "reference_context": [
                "infra/webui/server_lab_assistant_task_register.py",
                "infra/webui/server_ledger_actions.py",
            ],
            "reference_context_patterns": [
                "tests/control_plane/webui/test_lab_assistant_task_register.py",
                "tests/control_plane/webui/test_webui_ledger_actions.py",
            ],
            "forbidden_patterns": [
                "infra/pipeline/knowledge/**",
                "**/benchmark-gpt/**",
            ],
        },
        "domains": ["truth ownership", "durable state", "reconciliation"],
        "failure_modes": ["stale truth outranking canonical state", "silent overwrite", "schema drift"],
        "invariants": ["explicit precedence", "auditable writes", "canonical state not silently replaced"],
    },
    "retrieval": {
        "goal": "Interpret the prompt as retrieval, ranking, freshness, and evidence-first answer assembly rather than generic memory expansion.",
        "in_scope": [
            "search-plan quality",
            "retrieval applicability and freshness",
            "ranking or evidence precedence",
            "inspectable sources",
        ],
        "out_of_scope": [
            "scheduler internals",
            "UI shell ownership",
            "unrelated state persistence rewrites",
        ],
        "stage1": {
            "expected_families": ["retrieval_ranking_contract"],
            "accepted_families": ["lab_assistant_memory", "generic"],
            "forbidden_families": ["scheduler_safety"],
        },
        "stage2": {
            "expected_primary": "retrieval_ranking_contract",
            "partial_primaries": ["design", "code_modification", "feature"],
            "forbidden_primaries": ["system_hardening"],
        },
        "stage3": {
            "expected_subsystems": ["knowledge_retrieval", "retrieval_ranker", "answer_assembly"],
            "secondary_subsystems": ["query_shaping", "validation", "truth_ownership"],
            "forbidden_subsystems": ["scheduler_core", "schedule_calculation"],
        },
        "stage7": {
            "implementation_targets": [
                "infra/pipeline/stages/search_plan.py",
                "infra/pipeline/stages/repo_retriever.py",
                "infra/pipeline/planning.py",
            ],
            "target_patterns": [
                "infra/pipeline/stages/search_plan.py",
                "infra/pipeline/stages/repo_retriever.py",
                "infra/pipeline/planning.py",
                "infra/pipeline/knowledge/*.yaml",
                "tests/pipeline/test_stage3_search_plan_engine.py",
                "tests/pipeline/test_planning_flow.py",
            ],
            "reference_context": [
                "infra/pipeline/knowledge/conceptual_query_templates.yaml",
                "infra/pipeline/knowledge/task_profile_rules.yaml",
            ],
            "reference_context_patterns": [
                "tests/pipeline/test_phase2_task_classification.py",
                "tests/pipeline/test_task_archetype_profiles.py",
            ],
            "forbidden_patterns": [
                "infra/controlplane/**",
                "**/benchmark-gpt/**",
            ],
        },
        "domains": ["retrieval ranking", "evidence freshness", "answer assembly"],
        "failure_modes": ["stale evidence dominating", "source mixing", "missing evidence hidden as certainty"],
        "invariants": ["evidence beats stale memory", "sources remain inspectable", "freshness gaps are surfaced"],
    },
    "delegation": {
        "goal": "Interpret the prompt as agent delegation, coordination, or assistant role routing across controlled execution surfaces.",
        "in_scope": [
            "agent orchestration or routing",
            "delegation policy and bindings",
            "role coordination and task ownership",
            "operator-visible delegation state",
        ],
        "out_of_scope": [
            "pure scheduler internals unless explicitly requested",
            "content style tuning",
            "standalone UI polish",
        ],
        "stage1": {
            "expected_families": ["lab_assistant_delegation", "agent_orchestration_runtime"],
            "accepted_families": ["orchestration_checkpointing", "lab_assistant_memory", "generic"],
            "forbidden_families": ["retrieval_ranking_contract"],
        },
        "stage2": {
            "expected_primary": "feature",
            "partial_primaries": ["code_modification", "design", "orchestration_checkpointing"],
            "forbidden_primaries": ["retrieval_ranking_contract"],
        },
        "stage3": {
            "expected_subsystems": ["agent_orchestration", "task_register", "operator_entrypoints"],
            "secondary_subsystems": ["execution_reconciliation", "truth_ownership", "queue_dispatch"],
            "forbidden_subsystems": ["scheduler_core"],
        },
        "stage7": {
            "implementation_targets": [
                "infra/webui/server_agents.py",
                "infra/webui/server_agents_orchestrator.py",
                "infra/webui/server_lab_assistant_delegation_policy.py",
            ],
            "target_patterns": [
                "infra/webui/server_agents*.py",
                "infra/webui/server_lab_assistant_delegation_*.py",
                "infra/webui/server_lab_assistant_task_register.py",
                "tests/control_plane/webui/test_webui_agents_*.py",
                "tests/control_plane/webui/test_lab_assistant_delegation_*.py",
            ],
            "reference_context": [
                "infra/webui/server_lab_assistant_turns.py",
                "infra/webui/server_lab_assistant_routing_policy.py",
            ],
            "reference_context_patterns": [
                "tests/control_plane/webui/test_lab_assistant_task_register.py",
            ],
            "forbidden_patterns": [
                "infra/pipeline/knowledge/**",
                "**/benchmark-gpt/**",
            ],
        },
        "domains": ["delegation", "agent routing", "role coordination"],
        "failure_modes": ["wrong role assignment", "unbounded delegation drift", "missing ownership boundaries"],
        "invariants": ["delegation stays inspectable", "role boundaries stay explicit", "operator path remains authoritative"],
    },
    "ui": {
        "goal": "Interpret the prompt as operator-facing projection and UI-state integrity rather than backend or command semantics.",
        "in_scope": [
            "operator surfaces",
            "projection honesty",
            "state visibility and summaries",
            "frontend/backend truth separation",
        ],
        "out_of_scope": [
            "scheduler internals",
            "low-level transport work",
            "generic content generation",
        ],
        "stage1": {
            "expected_families": ["ui_local_changes"],
            "accepted_families": ["generic", "operator_control"],
            "forbidden_families": ["retrieval_ranking_contract"],
        },
        "stage2": {
            "expected_primary": "feature",
            "partial_primaries": ["code_modification", "design"],
            "forbidden_primaries": ["system_hardening"],
        },
        "stage3": {
            "expected_subsystems": ["operator_entrypoints", "operator_visible_state", "writeback_projection"],
            "secondary_subsystems": ["validation", "truth_ownership", "execution_reconciliation"],
            "forbidden_subsystems": ["scheduler_core"],
        },
        "stage7": {
            "implementation_targets": [
                "infra/webui/server_routes_and_main.py",
                "infra/webui/server_lab_assistant_web_operations.py",
                "infra/webui/server_lab_assistant_operator_snapshot.py",
            ],
            "target_patterns": [
                "infra/webui/server_routes_and_main.py",
                "infra/webui/server_lab_assistant_web_operations.py",
                "infra/webui/server_lab_assistant_operator_snapshot.py",
                "infra/webui/server_ui_metadata.py",
                "tests/control_plane/webui/test_webui_*.py",
            ],
            "reference_context": [
                "infra/webui/server_run_summaries.py",
                "infra/webui/server_timeline.py",
            ],
            "reference_context_patterns": [
                "tests/control_plane/webui/test_webui_lab_operations_console.py",
            ],
            "forbidden_patterns": [
                "infra/pipeline/knowledge/**",
                "**/benchmark-gpt/**",
            ],
        },
        "domains": ["operator UI", "projection state", "surface integrity"],
        "failure_modes": ["stale state shown as current", "backend ambiguity hidden", "projection owning truth"],
        "invariants": ["backend remains canonical", "projection state is explicit", "UI does not invent durable truth"],
    },
    "generic": {
        "goal": "Treat the prompt as a weakly matched software-change request and keep the benchmark expectations broad and conservative.",
        "in_scope": [
            "small-scope code modification",
            "operator-facing or validation-adjacent logic",
            "generic repo navigation signals",
        ],
        "out_of_scope": [
            "aggressive domain overfitting",
            "pretending the prompt is a precise repo-local contract",
        ],
        "stage1": {
            "expected_families": ["generic"],
            "accepted_families": ["api_control_plane", "admission_and_validation", "orchestration_checkpointing"],
            "forbidden_families": [],
        },
        "stage2": {
            "expected_primary": "code_modification",
            "partial_primaries": ["feature", "design", "system_hardening"],
            "forbidden_primaries": [],
        },
        "stage3": {
            "expected_subsystems": ["validation", "operator_entrypoints", "knowledge_retrieval"],
            "secondary_subsystems": ["state_persistence", "observability", "task_register"],
            "forbidden_subsystems": [],
        },
        "stage7": {
            "implementation_targets": [
                "infra/webui/server_routes_and_main.py",
                "infra/pipeline/stages/task_classifier.py",
                "infra/controlplane/scheduled_jobs.py",
            ],
            "target_patterns": [
                "infra/webui/*.py",
                "infra/controlplane/*.py",
                "infra/pipeline/stages/*.py",
                "tests/control_plane/**/*.py",
                "tests/pipeline/*.py",
            ],
            "reference_context": [
                "infra/pipeline/knowledge/task_families.yaml",
            ],
            "reference_context_patterns": [
                "infra/pipeline/knowledge/*.yaml",
            ],
            "forbidden_patterns": [
                "**/benchmark-gpt/**",
            ],
        },
        "domains": ["generic code change", "operator path", "validation"],
        "failure_modes": ["over-specific routing", "repo-gravity drift", "unjustified certainty"],
        "invariants": ["prefer conservative mapping", "avoid benchmark contamination"],
    },
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", str(text or "").strip()).strip("._-") or "item"


def _lower_blob(parts: Iterable[str]) -> str:
    return "\n".join(str(part or "") for part in parts).lower()


def _has_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def _keyword_hits(text: str, terms: Sequence[str]) -> int:
    return sum(1 for term in terms if term in text)


def _derive_profile(item: Mapping[str, Any]) -> Tuple[str, Dict[str, int]]:
    groups = [str(x).strip().lower() for x in list(item.get("groups") or []) if str(x).strip()]
    text = _lower_blob(
        [
            item.get("prompt"),
            item.get("source_repo"),
            item.get("source_file"),
            " ".join(str(x) for x in list(item.get("reasons") or [])),
            " ".join(groups),
        ]
    )
    scores = {
        "scheduler": 0,
        "checkpoint": 0,
        "truth": 0,
        "retrieval": 0,
        "delegation": 0,
        "ui": 0,
        "generic": 1,
    }

    for group in groups:
        if group == "orchestration":
            scores["scheduler"] += 3
            scores["checkpoint"] += 2
        elif group in {"truth_durable", "truth_core"}:
            scores["truth"] += 4
        elif group == "retrieval":
            scores["retrieval"] += 5
        elif group == "delegation":
            scores["delegation"] += 5
        elif group == "ui":
            scores["ui"] += 4

    scheduler_terms = ["scheduler", "schedule", "meeting", "calendar", "cron", "run_at", "queue", "dispatch", "flag", "rollout", "slot"]
    checkpoint_terms = ["restart", "crash", "checkpoint", "recovery", "resume", "timeout", "retry", "heartbeat", "stale claim", "idempot"]
    truth_terms = ["truth", "source of truth", "persist", "durable", "state =", "state:", "memory", "history", "identity", "reconcile", "receipt"]
    retrieval_terms = ["retriev", "search", "browse", "browser", "plugin", "source", "citation", "docs", "documentation", "query", "evidence"]
    delegation_terms = ["agent", "assistant", "router", "controller", "swarm", "delegat", "orchestrator", "worker", "tool"]
    ui_terms = ["streamlit", "frontend", "front-end", "ui", "page", "screen", "display", "dashboard", "poster", "visual"]

    scores["scheduler"] += _keyword_hits(text, scheduler_terms)
    scores["checkpoint"] += _keyword_hits(text, checkpoint_terms)
    scores["truth"] += _keyword_hits(text, truth_terms)
    scores["retrieval"] += _keyword_hits(text, retrieval_terms)
    scores["delegation"] += _keyword_hits(text, delegation_terms)
    scores["ui"] += _keyword_hits(text, ui_terms)

    if _has_any(text, ["resume", "cover letter", "recruit", "interview", "job search", "cv "]):
        scores["generic"] += 3
        scores["retrieval"] += 1
    if _has_any(text, ["feature flag", "evaluate", "referee", "score"]):
        scores["scheduler"] += 1
        scores["generic"] += 1
    if _has_any(text, ["plugin", "browser", "latest", "up-to-date"]):
        scores["retrieval"] += 3
    if _has_any(text, ["belief", "desire", "assistant's background", "persona"]):
        scores["delegation"] += 2
        scores["truth"] += 1
    if _has_any(text, ["drawer_open", "blocks_on_table", "state ="]):
        scores["truth"] += 3

    ordered = sorted(scores.items(), key=lambda item: (item[1], item[0]), reverse=True)
    profile = ordered[0][0]
    if profile == "scheduler" and scores["checkpoint"] >= scores["scheduler"] - 1:
        profile = "checkpoint"
    if profile == "truth" and scores["ui"] >= 3:
        profile = "ui"
    if profile == "generic" and groups:
        for preferred in ["retrieval", "delegation", "truth", "ui", "checkpoint", "scheduler"]:
            if scores[preferred] >= 3:
                profile = preferred
                break
    return profile, scores


def _confidence_label(item: Mapping[str, Any], profile_scores: Mapping[str, int]) -> Tuple[str, float]:
    numeric = float(item.get("score") or 0.0)
    top = max(profile_scores.values()) if profile_scores else 0
    if numeric >= 20.0 and top >= 5:
        return "high", 0.82
    if numeric >= 14.0 and top >= 4:
        return "medium", 0.68
    return "low", 0.52


def _merge_lists(*parts: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for part in parts:
        for raw in list(part or []):
            value = str(raw or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            out.append(value)
    return out


def _reference_doc_paths(profile: str) -> List[str]:
    mapping = {
        "scheduler": [PIPELINE_BOOTSTRAP_DOC],
        "checkpoint": [PIPELINE_BOOTSTRAP_DOC],
        "truth": [
            PIPELINE_BOOTSTRAP_DOC,
            LAB_AGENT_BOOTSTRAP_DOC,
        ],
        "retrieval": [PIPELINE_BOOTSTRAP_DOC],
        "delegation": [
            PIPELINE_BOOTSTRAP_DOC,
            LAB_AGENT_BOOTSTRAP_DOC,
        ],
        "ui": [PIPELINE_BOOTSTRAP_DOC],
        "generic": [PIPELINE_BOOTSTRAP_DOC],
    }
    return list(mapping.get(profile, [PIPELINE_BOOTSTRAP_DOC]))


def build_reference_record(item: Mapping[str, Any]) -> Dict[str, Any]:
    case_id = Path(str(item.get("output_file") or "")).stem
    title = f"PromptSet Candidate {int(item.get('rank') or 0):03d}" if str(item.get("rank") or "").isdigit() else case_id
    profile, profile_scores = _derive_profile(item)
    profile_template = PROFILE_TEMPLATES[profile]
    confidence_label, confidence_value = _confidence_label(item, profile_scores)
    prompt_text = str(item.get("prompt") or "").strip()
    stage1_expectations = dict(profile_template["stage1"])
    if confidence_label == "low":
        stage1_expectations["accepted_families"] = _merge_lists(
            list(stage1_expectations.get("accepted_families") or []),
            ["generic"],
        )

    lexical_queries = _merge_lists(
        [str(item.get("source_file") or "").replace("~", "/")],
        [str(item.get("source_repo") or "")],
        profile_template["stage7"]["implementation_targets"],
    )[:8]
    conceptual_queries = [
        profile_template["goal"],
        f"Map this prompt to {', '.join(profile_template['domains'][:2])} surfaces in the repo.",
    ]

    return {
        "case": {
            "id": case_id,
            "title": title,
            "mode": "gpt_reference",
            "source_repo": str(item.get("source_repo") or ""),
            "source_file": str(item.get("source_file") or ""),
            "selection_groups": list(item.get("groups") or []),
            "selection_reasons": list(item.get("reasons") or []),
            "selection_rank": int(item.get("rank") or 0),
            "selection_score": float(item.get("score") or 0.0),
            "reference_profile": profile,
            "reference_confidence": confidence_label,
        },
        "task_understanding": {
            "normalized_goal": profile_template["goal"],
            "in_scope": list(profile_template["in_scope"]),
            "out_of_scope": list(profile_template["out_of_scope"]),
            "prompt_excerpt": prompt_text[:600],
        },
        "reasoning": {
            "primary_domains": list(profile_template["domains"]),
            "subsystems": list(profile_template["stage3"]["expected_subsystems"]) + list(profile_template["stage3"]["secondary_subsystems"]),
            "failure_modes": list(profile_template["failure_modes"]),
            "invariants": list(profile_template["invariants"]),
        },
        "retrieval_plan": {
            "lexical_queries": lexical_queries,
            "conceptual_queries": conceptual_queries,
            "subsystem_hypotheses": list(profile_template["stage3"]["expected_subsystems"]) + list(profile_template["stage3"]["secondary_subsystems"]),
            "negative_targets": _merge_lists(profile_template["stage7"]["forbidden_patterns"], COMMON_NEGATIVE_TARGETS),
        },
        "candidate_files": {
            "implementation_targets": [
                {"path": path, "rationale": f"Primary {profile} implementation surface", "confidence": 0.72}
                for path in list(profile_template["stage7"]["implementation_targets"])
            ],
            "implementation_target_patterns": [
                {"pattern": pattern, "rationale": f"Accepted {profile} implementation bundle"}
                for pattern in list(profile_template["stage7"]["target_patterns"])
            ],
            "reference_context": [
                {"path": path, "rationale": f"Useful {profile} context or adjacent ownership surface"}
                for path in list(profile_template["stage7"]["reference_context"])
            ],
            "reference_context_patterns": [
                {"pattern": pattern, "rationale": f"Accepted {profile} contextual bundle"}
                for pattern in list(profile_template["stage7"]["reference_context_patterns"])
            ],
            "forbidden_or_demoted": [
                {"path_or_pattern": pattern, "reason": "Out of scope or benchmark contamination"}
                for pattern in _merge_lists(profile_template["stage7"]["forbidden_patterns"], COMMON_NEGATIVE_TARGETS)
            ],
        },
        "stage_expectations": {
            "stage1": stage1_expectations,
            "stage2": dict(profile_template["stage2"]),
            "stage3": dict(profile_template["stage3"]),
            "stage7": {
                "scoring_mode": "pattern_bundle",
                "implementation_targets": list(profile_template["stage7"]["implementation_targets"]),
                "implementation_target_patterns": list(profile_template["stage7"]["target_patterns"]),
                "reference_context": list(profile_template["stage7"]["reference_context"]),
                "reference_context_patterns": list(profile_template["stage7"]["reference_context_patterns"]),
                "forbidden_patterns": _merge_lists(profile_template["stage7"]["forbidden_patterns"], COMMON_NEGATIVE_TARGETS),
            },
        },
        "confidence": {
            "overall": confidence_value,
            "label": confidence_label,
            "notes": [
                f"profile={profile}",
                f"profile_scores={json.dumps(profile_scores, sort_keys=True)}",
                f"selection_score={float(item.get('score') or 0.0):.1f}",
                f"docs_context={','.join(_reference_doc_paths(profile))}",
            ],
        },
    }


def build_references(selection_manifest_path: Path, out_dir: Path) -> Dict[str, Any]:
    manifest = json.loads(selection_manifest_path.read_text(encoding="utf-8"))
    items = list(manifest.get("items") or [])
    out_dir.mkdir(parents=True, exist_ok=True)

    refs: List[Dict[str, Any]] = []
    profile_counts: Dict[str, int] = {}
    for item in items:
        record = build_reference_record(item)
        case_id = str(record["case"]["id"])
        profile = str(record["case"]["reference_profile"])
        profile_counts[profile] = profile_counts.get(profile, 0) + 1
        target = out_dir / f"{case_id}__reference_trace.yaml"
        target.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True), encoding="utf-8")
        refs.append(
            {
                "case_id": case_id,
                "title": str(record["case"]["title"]),
                "profile": profile,
                "confidence": str(record["case"]["reference_confidence"]),
                "path": str(target),
            }
        )

    aggregate = {
        "source_manifest": str(selection_manifest_path),
        "reference_dir": str(out_dir),
        "selected_count": len(items),
        "profile_counts": dict(sorted(profile_counts.items())),
        "references": refs,
    }
    (out_dir / "reference_manifest.json").write_text(json.dumps(aggregate, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        "# PromptSet GPT Reference Manifest",
        "",
        f"- source_manifest: `{selection_manifest_path}`",
        f"- selected_count: `{len(items)}`",
        "",
        "## Profile Counts",
        "",
    ]
    for key, value in sorted(profile_counts.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## References", ""])
    for ref in refs:
        lines.append(f"- `{ref['case_id']}` | `{ref['profile']}` | `{ref['confidence']}` | `{ref['path']}`")
    (out_dir / "reference_manifest.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return aggregate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build GPT-reference traces for the PromptSet benchmark subset.")
    parser.add_argument(
        "--selection-manifest",
        default="tests/benchmarks/pipeline/benchmark-gpt/promptSet/prompts/selection_manifest.json",
    )
    parser.add_argument(
        "--out-dir",
        default="tests/benchmarks/pipeline/benchmark-gpt/promptSet/gpt_references",
    )
    args = parser.parse_args(argv)
    aggregate = build_references(Path(args.selection_manifest), Path(args.out_dir))
    print(json.dumps(aggregate, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
