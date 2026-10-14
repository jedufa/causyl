from __future__ import annotations

import json
from pathlib import Path

from infra.pipeline.planning import bundle_context, maybe_model_patch_plan, rank_context
from infra.pipeline.core import SubprocessResult
from infra.pipeline.stages.repo_retriever import build_retrieval
from infra.pipeline.stages.repo_retriever import _collect_rg_hits, _collect_rg_hits_batched
from infra.pipeline.stages.search_plan import build_search_plan
from infra.pipeline.stages.job_intake import build_job_contract
from infra.pipeline.stages.task_classifier import classify


def _base_config() -> dict:
    return {
        "limits": {
            "min_file_score": 1,
            "max_selected_files": 6,
            "max_snippets": 12,
            "max_bundle_files": 6,
            "max_bundle_snippets": 12,
            "max_snippet_chars": 1200,
            "max_total_context_chars": 12000,
            "max_primary_targets": 4,
            "max_targets_per_subsystem": 2,
            "score_margin_keep_secondary": 0.15,
        },
        "semantic": {
            "semantic_score_weight": 0.35,
            "semantic_mode": "off",
            "semantic_provider": "none",
            "enable_semantic_retrieval": False,
        },
        "planning": {"enable_model": False},
        "_stage": {"allow_remote_model": False},
    }


def _lab_assistant_memory_prompt() -> dict:
    return {
        "title": "Research Memory, Knowledge, And Cross-Project Learning For Lab Assistant v2+",
        "description": (
            "# Patch Instruction Sheet (PIS)\n\n"
            "**UID:** PIS-20260313-145246Z-F2R6\n\n"
            "## 1. Title\n"
            "Research Memory, Knowledge, And Cross-Project Learning For Lab Assistant v2+\n\n"
            "## 2. Goal / user-visible behavior\n"
            "Define the parent workstream that lets the laboratory remember, retrieve, correct, and reuse prior work across projects "
            "without letting summaries outrank evidence, stale conclusions steer new work, or cross-project memory turn into a second planning authority. "
            "Memory-backed answers carry confidence, provenance, freshness, and correction semantics. Retrieval can span reports, artifacts, transcripts, ledger records, and prior workcell outcomes while keeping canonical source records inspectable. "
            "Chapter 4 expands what the lab can remember and learn from without redefining Chapter 1 task/runtime truth.\n\n"
            "## 6. Source-of-truth and ownership boundaries\n"
            "This parent owns memory record shape, extraction eligibility, confidence correction expiry semantics, retrieval ranking among memory and source evidence, and cross-project reuse boundaries. "
            "Memory may summarize, index, compare, and point at canonical evidence, but it must not become a rival mutable task store, a fake source of execution truth, or a justification engine that overrides fresher direct evidence."
        ),
        "objective": (
            "Define user-visible contract and decomposition-ready child boundaries for task register, truth ownership, execution reconciliation, "
            "transcript evidence, ledger provenance, writeback boundaries, and contradiction handling."
        ),
        "constraints": [],
        "acceptance_criteria": [],
        "issue_context": {},
        "task_type_hint": "unknown",
    }


def _assistant_truth_contract_prompt() -> dict:
    return {
        "title": "Persistent Assistant Identity and Source-of-Truth Contract",
        "description": (
            "Define and enforce a durable assistant identity and runtime-state contract that survives fresh turns, uses a versioned schema, "
            "records append-only state mutations, applies deterministic write arbitration, exposes blocked and recovery-required states, "
            "and makes ledger versus runtime versus chat truth precedence explicit and testable."
        ),
        "objective": (
            "Define durable assistant identity reload, append-only assistant event evidence, source-of-truth precedence, and deterministic write arbitration."
        ),
        "constraints": [],
        "acceptance_criteria": [],
        "issue_context": {},
        "task_type_hint": "unknown",
    }


def _candidate(
    path: str,
    *,
    lexical: float,
    structural: float,
    graph: float,
    matched_terms: list[str],
    covers: list[str],
    matched_symbols: list[str] | None = None,
    evidence_sources: list[str] | None = None,
) -> dict:
    total = lexical + structural + graph
    symbols = list(matched_symbols or [])
    return {
        "path": path,
        "scores": {
            "lexical_score": lexical,
            "structural_score": structural,
            "graph_score": graph,
            "recency_score": 0.0,
            "semantic_score": 0.0,
            "final_score": total,
            "retrieval_sort_score": total,
        },
        "matched_terms": list(matched_terms),
        "matched_symbols": symbols,
        "line_hits": [
            {
                "start_line": 1,
                "end_line": 1,
                "preview": " ".join(list(matched_terms) + symbols)[:240],
            }
        ],
        "reason_codes": ["keyword_match"],
        "semantic_evidence": [],
        "evidence_sources": list(evidence_sources or ["deterministic"]),
        "covers": list(covers),
        "coverage_score": float(len(covers)),
        "superficial_match_penalty": 0.0,
    }


def test_search_plan_filters_low_signal_and_uses_current_scheduler_anchors() -> None:
    job_contract = {
        "title": "Make my scheduler more robust against failures, especially human failures",
        "description": "Make my scheduler more robust against failures, especially human failures. Consider all failure cases and mitigate each of them.",
        "objective": "Make my scheduler more robust against failures, especially human failures. Consider all failure cases and mitigate each of them.",
        "issue_context": {},
        "constraints": [],
        "acceptance_criteria": [],
    }
    task_profile = {
        "primary_task_type": "system_hardening",
        "secondary_tags": ["system_hardening"],
        "task_families": ["scheduler_safety", "scheduler_hardening"],
        "replace_spec": None,
        "retrieval_bias": {
            "include": ["scheduler_core", "schedule_calculation", "lease_claims", "state_persistence", "validation", "audit_receipts"],
            "exclude": ["docs", "reports", "runtime"],
        },
        "cross_cutting_flags": ["state_consistency", "operator_safety", "recovery_integrity"],
    }

    result = build_search_plan(job_contract, task_profile, max_candidates=100)
    queries = result["search_plan"]["queries"]
    query_texts = {entry["query"] for entry in queries}
    query_types = {entry["query"]: entry["query_type"] for entry in queries}

    assert "scheduler_watchdog.py" in query_texts
    assert "scheduler_pipeline_healer.py" in query_texts
    assert "scheduler_recovery_assistant.py" in query_texts
    assert "scheduler_schedule_job.py" in query_texts
    assert "scheduler_verify_job.py" in query_texts
    assert "scheduled_jobs.py" not in query_texts
    assert "server_maintenance.py" not in query_texts
    assert "each" not in query_texts
    assert "them" not in query_texts
    assert query_types["failures"] == "literal"
    assert result["search_plan"]["search_mode"] == "targeted_ranked_context"
    assert "docs/" in result["search_plan"]["negative_targets"]
    assert "reports/" in result["search_plan"]["negative_targets"]
    assert "runtime/" in result["search_plan"]["negative_targets"]
    assert "schedule_calculation" in result["search_plan"]["subsystem_hypotheses"]
    assert any("manual control actions" in entry.lower() for entry in result["search_plan"]["conceptual_queries"])
    assert any("due time" in entry.lower() for entry in result["search_plan"]["conceptual_queries"])
    assert "scheduler watchdog pipeline healer" in result["search_plan"]["semantic_query_text"]
    assert "task_families=scheduler_safety,scheduler_hardening" in result["search_plan"]["notes"]
    assert any(note.startswith("retrieval_bias.include=") for note in result["search_plan"]["notes"])
    assert any(note.startswith("cross_cutting_flags=") for note in result["search_plan"]["notes"])
    assert result["intake_assessment"]["failure_taxonomy"]["operator_failure_modes"] == []


def test_retrieval_honors_filename_queries_and_excludes_docs_from_symbol_for_non_doc_task(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / "docs").mkdir(parents=True)
    (repo_root / "infra/bin/scheduler_watchdog.py").write_text(
        "def retry_watchdog():\n    return 'watchdog retry'\n",
        encoding="utf-8",
    )
    (repo_root / "docs/guide.md").write_text("scheduler retry reference only\n", encoding="utf-8")

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "allowed_paths": [],
            "forbidden_paths": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "refactor",
            "task_families": ["scheduler_hardening"],
            "secondary_tags": ["scheduler_hardening"],
        },
        "search_plan": {
            "queries": [
                {"query": "scheduler_watchdog.py", "query_type": "filename", "weight": 0.95},
                {"query": "retry", "query_type": "symbol", "weight": 0.86},
            ],
            "search_mode": "targeted_ranked_context",
            "semantic_query_text": "scheduler watchdog retry",
        },
        "extracted_terms": {"file_hints": []},
    }

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=_base_config())["retrieval"]
    paths = [entry["path"] for entry in retrieval["file_candidates"]]
    candidate_by_path = {entry["path"]: entry for entry in retrieval["file_candidates"]}

    assert "infra/bin/scheduler_watchdog.py" in paths
    assert "docs/guide.md" not in paths
    assert candidate_by_path["infra/bin/scheduler_watchdog.py"]["scores"]["graph_score"] > 0


def test_retrieval_consumes_semantic_score_and_chunk_preview(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / "infra/bin/scheduler_watchdog.py").write_text(
        "def retry_watchdog():\n    return 'watchdog retry'\n",
        encoding="utf-8",
    )

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "allowed_paths": [],
            "forbidden_paths": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "refactor",
            "task_families": ["scheduler_hardening"],
            "secondary_tags": ["scheduler_hardening"],
        },
        "search_plan": {
            "queries": [
                {"query": "watchdog", "query_type": "symbol", "weight": 0.86},
            ],
            "search_mode": "targeted_ranked_context",
            "semantic_query_text": "scheduler recovery watchdog",
        },
        "extracted_terms": {"file_hints": []},
    }
    config = _base_config()
    config["semantic"].update(
        {
            "enable_semantic_retrieval": True,
            "semantic_mode": "rank_only",
            "semantic_provider": "stub",
        }
    )

    def fake_semantic_retrieve(**_: object):
        return (
            [
                {
                    "path": "infra/bin/scheduler_watchdog.py",
                    "semantic_score": 0.91,
                    "chunk_preview": "watchdog recovery reconciles stale claims",
                }
            ],
            {
                "semantic_status": "ok",
                "effective_mode": "rank_only",
                "effective_provider": "stub",
                "effective_model": "all-minilm",
                "requested_mode": "rank_only",
                "requested_provider": "stub",
                "requested_model": "all-minilm",
                "effective_strategy": "semantic_rank_only",
                "semantic_returned": 1,
            },
        )

    monkeypatch.setattr("infra.pipeline.stages.repo_retriever.semantic_retrieve", fake_semantic_retrieve)

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=config)["retrieval"]
    candidate = next(entry for entry in retrieval["file_candidates"] if entry["path"] == "infra/bin/scheduler_watchdog.py")

    assert candidate["scores"]["semantic_score"] > 0.91
    assert retrieval["semantic"]["results"][0]["semantic_score_adjusted"] == candidate["scores"]["semantic_score"]
    assert candidate["semantic_evidence"][0]["preview"] == "watchdog recovery reconciles stale claims"
    assert "semantic" in candidate["evidence_sources"]
    assert candidate["scores"]["retrieval_sort_score"] > candidate["scores"]["final_score"]


def test_truth_contract_flow_collapses_to_owner_surfaces_local_contract_and_validation_only(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/webui").mkdir(parents=True)
    (repo_root / "tests/control_plane/webui").mkdir(parents=True)
    (repo_root / "docs").mkdir(parents=True)
    (repo_root / "governance/agents/continuity/lab_assistant_v1").mkdir(parents=True)

    (repo_root / "infra/webui/server_lab_assistant_state.py").write_text(
        "def resolve_source_precedence():\n"
        "    assistant_id = 'alpha'\n"
        "    write_generation = 0\n"
        "    recovery_marker = None\n"
        "    current_state = 'blocked'\n",
        encoding="utf-8",
    )
    (repo_root / "tests/control_plane/webui/test_lab_assistant_state.py").write_text(
        "def test_assistant_state_reload():\n    assert True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_task_register.py").write_text(
        "task register ledger truth boundary\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_operator_snapshot.py").write_text(
        "snapshot projection of assistant state\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_turns.py").write_text(
        "chat transcript and turns projection\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_routes_and_main.py").write_text(
        "read only admin inspection route\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_host.py").write_text(
        "host durability baseline and blocked initialization\n",
        encoding="utf-8",
    )
    (repo_root / "docs/lab_assistant_identity_contract.md").write_text(
        "# lab assistant identity contract\nsource of truth precedence and append-only audit events\n",
        encoding="utf-8",
    )
    (repo_root / "governance/agents/continuity/lab_assistant_v1/current.md").write_text(
        "broader governance context\n",
        encoding="utf-8",
    )

    prompt = _assistant_truth_contract_prompt()
    config = _base_config()
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    stage3 = build_search_plan(prompt, task_profile, max_candidates=100, intake_assessment=intake)
    search_plan = stage3["search_plan"]
    result_obj = {
        "job_contract": {**prompt, "repo_root": str(repo_root), "allowed_paths": [], "forbidden_paths": []},
        "intake_assessment": intake,
        "task_profile": task_profile,
        "search_plan": search_plan,
        "extracted_terms": {"file_hints": []},
    }
    result_obj["retrieval"] = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=config)["retrieval"]
    ranked = rank_context(result_obj, config=config)
    implementation_targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]]
    reference_context = [entry["path"] for entry in ranked["ranked_context"]["reference_context"]]

    assert task_profile["primary_task_type"] == "truth_contract"
    assert implementation_targets == [
        "infra/webui/server_lab_assistant_state.py",
        "tests/control_plane/webui/test_lab_assistant_state.py",
    ]
    assert "docs/lab_assistant_identity_contract.md" in reference_context
    assert "infra/webui/server_lab_assistant_task_register.py" not in implementation_targets
    assert "infra/webui/server_lab_assistant_operator_snapshot.py" not in implementation_targets
    assert "infra/webui/server_lab_assistant_turns.py" not in implementation_targets

    bundled = bundle_context({**result_obj, **ranked}, config=config)
    patch_plan, warnings = maybe_model_patch_plan({**result_obj, **ranked, **bundled}, config=config)

    assert warnings
    assert patch_plan["plan_status"] == "VALIDATION_ONLY"
    assert [entry["path"] for entry in patch_plan["modify_files"]] == ["tests/control_plane/webui/test_lab_assistant_state.py"]
    assert any("Preserve the dominant truth-contract owner" in step for step in patch_plan["implementation_steps"])
    assert patch_plan["required_tests"]


def test_rank_bundle_and_patch_plan_respect_target_reference_contract(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / "docs").mkdir(parents=True)
    (repo_root / "infra/bin/scheduler_watchdog.py").write_text(
        "def retry_job():\n    return 'retry'\n\n\ndef recover_job():\n    return 'recover'\n",
        encoding="utf-8",
    )
    (repo_root / "docs/design.md").write_text("scheduler watchdog design notes\n", encoding="utf-8")

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "title": "Harden scheduler",
            "description": "Make scheduler watchdog and recovery logic more robust.",
            "objective": "Make scheduler watchdog and recovery logic more robust.",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "refactor",
            "secondary_tags": ["scheduler_hardening"],
            "task_families": ["scheduler_hardening"],
            "ambiguity_score": 0.2,
        },
        "retrieval": {
            "file_candidates": [
                {
                    "path": "infra/bin/scheduler_watchdog.py",
                    "scores": {
                        "lexical_score": 3.0,
                        "structural_score": 2.0,
                        "graph_score": 1.0,
                        "recency_score": 0.0,
                        "semantic_score": 0.0,
                        "final_score": 6.0,
                        "retrieval_sort_score": 6.0,
                    },
                    "matched_terms": ["scheduler", "watchdog", "recovery"],
                    "matched_symbols": ["retry_job", "recover_job"],
                    "line_hits": [
                        {"start_line": 1, "end_line": 1, "preview": "def retry_job():"},
                        {"start_line": 5, "end_line": 5, "preview": "def recover_job():"},
                    ],
                    "reason_codes": ["symbol_match", "path_hint"],
                    "semantic_evidence": [],
                    "evidence_sources": ["deterministic"],
                },
                {
                    "path": "docs/design.md",
                    "scores": {
                        "lexical_score": 2.0,
                        "structural_score": 5.0,
                        "graph_score": 0.0,
                        "recency_score": 0.0,
                        "semantic_score": 0.0,
                        "final_score": 7.0,
                        "retrieval_sort_score": 7.0,
                    },
                    "matched_terms": ["scheduler", "watchdog"],
                    "matched_symbols": [],
                    "line_hits": [{"start_line": 1, "end_line": 1, "preview": "scheduler watchdog design notes"}],
                    "reason_codes": ["keyword_match"],
                    "semantic_evidence": [],
                    "evidence_sources": ["deterministic"],
                },
            ],
            "semantic": {
                "runtime": {
                    "effective_mode": "off",
                    "requested_mode": "off",
                    "effective_provider": "none",
                    "requested_provider": "none",
                    "requested_model": "all-minilm",
                    "effective_model": "all-minilm",
                    "semantic_status": "disabled",
                }
            },
            "retrieval_provenance": {"strategy": "deterministic_only"},
        },
    }

    ranked = rank_context(result_obj, _base_config())
    ranked_context = ranked["ranked_context"]
    assert [entry["path"] for entry in ranked_context["implementation_targets"]] == ["infra/bin/scheduler_watchdog.py"]
    assert [entry["path"] for entry in ranked_context["reference_context"]] == ["docs/design.md"]
    assert ranked_context["selected_files"][0]["role"]
    assert ranked_context["selected_files"][0]["edit_necessity_score"] is not None
    assert ranked_context["confidence"]["stage"] == "script_5_context_ranker"

    bundled = bundle_context(ranked, _base_config())
    context_bundle = bundled["context_bundle"]
    assert [entry["path"] for entry in context_bundle["editable_targets"]] == ["infra/bin/scheduler_watchdog.py"]
    assert [entry["path"] for entry in context_bundle["read_only_context"]] == ["docs/design.md"]
    assert context_bundle["bundle_gap_warning"] is False
    assert context_bundle["coverage_validation"]["editable_target_count"] == 1
    assert context_bundle["editable_targets"][0]["ownership_confidence"]["stage"] == "script_6_context_bundle.file"
    first_snippet = context_bundle["editable_targets"][0]["snippets"][0]
    assert first_snippet["snippet_id"]
    assert first_snippet["allowed_edit_region"]

    patch_plan, warnings = maybe_model_patch_plan(bundled, _base_config())
    assert not warnings or all(isinstance(item, str) for item in warnings)
    assert [entry["path"] for entry in patch_plan["modify_files"]] == ["infra/bin/scheduler_watchdog.py"]
    assert patch_plan["patch_execution_contract"]["implementation_targets"][0]["path"] == "infra/bin/scheduler_watchdog.py"
    assert "docs/" in patch_plan["forbidden_targets"]
    assert patch_plan["execution_status"] in {"READY_FOR_EXECUTION", "NEEDS_OPERATOR_DECISION"}
    assert patch_plan["cross_cutting_contract"]["invariants"] == []
    assert patch_plan["must_not_change"]
    assert patch_plan["change_boundaries"] == ["infra/bin/scheduler_watchdog.py"]
    assert patch_plan["confidence"]["stage"] == "script_7_patch_plan"


def test_retrieval_respects_gitignore_and_excludes_generated_artifacts(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / "reports/go-123/deterministic").mkdir(parents=True)
    (repo_root / "runtime/live").mkdir(parents=True)
    (repo_root / ".gitignore").write_text("/runtime/live/\n", encoding="utf-8")
    (repo_root / "infra/bin/scheduler_watchdog.py").write_text("def retry_watchdog():\n    return True\n", encoding="utf-8")
    (repo_root / "reports/go-123/deterministic/summary.json").write_text('{"status": "old"}', encoding="utf-8")
    (repo_root / "runtime/live/transient.py").write_text("def should_not_be_seen():\n    pass\n", encoding="utf-8")

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "allowed_paths": [],
            "forbidden_paths": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "refactor",
            "task_families": ["scheduler_hardening"],
            "secondary_tags": ["scheduler_hardening"],
        },
        "search_plan": {
            "queries": [
                {"query": "scheduler_watchdog.py", "query_type": "filename", "weight": 1.0},
                {"query": "summary", "query_type": "literal", "weight": 0.8},
                {"query": "transient", "query_type": "literal", "weight": 0.8},
            ],
            "search_mode": "targeted_ranked_context",
            "semantic_query_text": "scheduler watchdog retry",
        },
        "extracted_terms": {"file_hints": ["summary.json", "transient.py"]},
    }

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=_base_config())["retrieval"]
    paths = [entry["path"] for entry in retrieval["file_candidates"]]

    assert "infra/bin/scheduler_watchdog.py" in paths
    assert "reports/go-123/deterministic/summary.json" not in paths
    assert "runtime/live/transient.py" not in paths
    assert retrieval["retrieval_stats"]["gitignore_respected"] is True



def test_retrieval_respects_search_plan_negative_targets_even_when_queries_match(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / "docs").mkdir(parents=True)
    (repo_root / "infra/bin/scheduler_watchdog.py").write_text("def retry_watchdog():\n    return True\n", encoding="utf-8")
    (repo_root / "docs/scheduler_watchdog.md").write_text("scheduler watchdog design notes\n", encoding="utf-8")

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "allowed_paths": [],
            "forbidden_paths": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "system_hardening",
            "task_families": ["scheduler_safety"],
            "secondary_tags": ["system_hardening"],
        },
        "search_plan": {
            "queries": [
                {"query": "retry_watchdog", "query_type": "symbol", "weight": 1.0},
                {"query": "scheduler_watchdog.md", "query_type": "filename", "weight": 1.0},
            ],
            "negative_targets": ["docs/", "*.md"],
            "search_mode": "targeted_ranked_context",
            "semantic_query_text": "scheduler watchdog retry",
        },
        "extracted_terms": {"file_hints": []},
    }

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=_base_config())["retrieval"]
    paths = [entry["path"] for entry in retrieval["file_candidates"]]

    assert "infra/bin/scheduler_watchdog.py" in paths
    assert "docs/scheduler_watchdog.md" not in paths
    assert retrieval["retrieval_stats"]["negative_targets_applied"] == ["docs/", "*.md"]


def test_semantic_rank_only_retrieval_uses_current_semantic_interface(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / ".gitignore").write_text("/runtime/live/\n", encoding="utf-8")
    (repo_root / "infra/bin/scheduler_watchdog.py").write_text(
        "def retry_watchdog():\n    return True\n", encoding="utf-8"
    )

    calls = []

    def fake_semantic_retrieve(*, repo_root, query_text, config, allowed_paths, forbidden_paths, candidate_paths=None):
        calls.append({
            "repo_root": str(repo_root),
            "query_text": query_text,
            "allowed_paths": list(allowed_paths),
            "forbidden_paths": list(forbidden_paths),
            "candidate_paths": list(candidate_paths or []),
        })
        return ([{
            "path": "infra/bin/scheduler_watchdog.py",
            "score": 0.91,
            "evidence": [{"line": 1, "preview": "def retry_watchdog():", "score": 0.91}],
        }], {
            "semantic_status": "ok",
            "requested_mode": "rank_only",
            "effective_mode": "rank_only",
            "requested_provider": "stub",
            "effective_provider": "stub",
            "requested_model": "all-minilm",
            "effective_model": "all-minilm",
            "requested_enabled": True,
            "effective_enabled": True,
            "semantic_returned": 1,
            "effective_strategy": "semantic_rank_only",
        })

    monkeypatch.setattr("infra.pipeline.stages.repo_retriever.semantic_retrieve", fake_semantic_retrieve)

    cfg = _base_config()
    cfg["semantic"] = {
        **cfg["semantic"],
        "semantic_mode": "rank_only",
        "semantic_provider": "stub",
        "enable_semantic_retrieval": True,
        "semantic_rank_only_candidate_limit": 10,
    }

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "allowed_paths": [],
            "forbidden_paths": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "refactor",
            "task_families": ["scheduler_hardening"],
            "secondary_tags": ["scheduler_hardening"],
        },
        "search_plan": {
            "queries": [
                {"query": "scheduler_watchdog.py", "query_type": "filename", "weight": 1.0},
            ],
            "search_mode": "targeted_ranked_context",
            "semantic_query_text": "scheduler watchdog retry",
        },
        "extracted_terms": {"file_hints": []},
    }

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=cfg)["retrieval"]

    assert calls, "semantic_retrieve should be called in rank_only mode"
    assert calls[0]["candidate_paths"] == ["infra/bin/scheduler_watchdog.py"]
    assert retrieval["semantic"]["status"] == "ok"
    assert retrieval["retrieval_stats"]["semantic_mode_effective"] == "rank_only"
    assert retrieval["file_candidates"][0]["path"] == "infra/bin/scheduler_watchdog.py"


def test_scheduler_canonical_family_prefers_scheduler_surfaces_in_retrieval_and_ranking(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / "tools/pipeline").mkdir(parents=True)
    (repo_root / "infra/bin/scheduler_schedule_job.py").write_text("def schedule_job():\n    return True\n", encoding="utf-8")
    (repo_root / "infra/bin/scheduler_recovery_assistant.py").write_text("def recover_scheduler_job():\n    return True\n", encoding="utf-8")
    (repo_root / "infra/bin/scheduler_pipeline_healer.py").write_text("def heal_pipeline():\n    return True\n", encoding="utf-8")
    (repo_root / "infra/bin/scheduler_verify_job.py").write_text("def verify_job():\n    return True\n", encoding="utf-8")
    (repo_root / "infra/pipeline/planning.py").write_text("def generic_planning():\n    return True\n", encoding="utf-8")

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "title": "make my scheduler more robust against failures, especially human failures",
            "description": "make my scheduler more robust against failures, especially human failures",
            "objective": "make my scheduler more robust against failures, especially human failures",
            "allowed_paths": [],
            "forbidden_paths": [],
            "issue_context": {},
            "constraints": [],
            "acceptance_criteria": [],
        },
        "task_profile": {
            "primary_task_type": "system_hardening",
            "task_families": ["scheduler_safety", "scheduler_hardening"],
            "secondary_tags": ["system_hardening"],
            "ambiguity_score": 0.2,
        },
        "search_plan": build_search_plan(
            {
                "title": "make my scheduler more robust against failures, especially human failures",
                "description": "make my scheduler more robust against failures, especially human failures",
                "objective": "make my scheduler more robust against failures, especially human failures",
                "issue_context": {},
                "constraints": [],
                "acceptance_criteria": [],
            },
            {
                "primary_task_type": "system_hardening",
                "task_families": ["scheduler_safety", "scheduler_hardening"],
                "secondary_tags": ["system_hardening"],
                "replace_spec": None,
            },
            max_candidates=50,
        )["search_plan"],
        "extracted_terms": {"file_hints": []},
    }

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=_base_config())["retrieval"]
    ranked = rank_context({**result_obj, "retrieval": retrieval}, _base_config())["ranked_context"]
    selected = [entry["path"] for entry in ranked["selected_files"]]

    assert selected[0] == "infra/bin/scheduler_recovery_assistant.py"
    assert "infra/bin/scheduler_pipeline_healer.py" in selected[:4]
    assert set(selected[:4]) == {
        "infra/bin/scheduler_recovery_assistant.py",
        "infra/bin/scheduler_pipeline_healer.py",
        "infra/bin/scheduler_schedule_job.py",
        "infra/bin/scheduler_verify_job.py",
    }
    assert "infra/pipeline/planning.py" not in selected[:4]


def test_scheduler_hardening_failure_taxonomy_promotes_healer_over_analysis_surface(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / "infra/bin/scheduler_schedule_job.py").write_text(
        "def schedule_due_jobs():\n"
        "    update_job_state()\n"
        "    dispatch_ready_job()\n"
        "    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/bin/scheduler_recovery_assistant.py").write_text(
        "def recover_scheduler_job():\n"
        "    reconcile_failed_run()\n"
        "    reclaim_stale_claim()\n"
        "    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/bin/scheduler_pipeline_healer.py").write_text(
        "def heal_scheduler_pipeline():\n"
        "    detect_findings()\n"
        "    reconcile_orphaned_run()\n"
        "    repair_stale_claim()\n"
        "    reclaim_lease()\n"
        "    update_job_state()\n"
        "    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/bin/scheduler_verify_job.py").write_text(
        "def verify_job_transition():\n"
        "    validate_manual_action()\n"
        "    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/bin/scheduler_analyze_job.py").write_text(
        "def analyze_job_history():\n"
        "    report_state_summary()\n"
        "    list_failures()\n"
        "    return True\n",
        encoding="utf-8",
    )

    job_contract = {
        "repo_root": str(repo_root),
        "title": "make my scheduler more robust against failures, especially human failures",
        "description": "make my scheduler more robust against failures, especially human failures",
        "objective": "make my scheduler more robust against failures, especially human failures",
        "allowed_paths": [],
        "forbidden_paths": [],
        "issue_context": {},
        "constraints": [],
        "acceptance_criteria": [],
    }
    intake_assessment = {
        "failure_taxonomy": {
            "operator_failure_modes": ["manual cancel races automatic retry", "forced restart without validation"],
            "recovery_failure_modes": ["stale claim not reclaimed", "orphaned run not reconciled", "checkpoint resume uses stale state"],
            "concurrency_failure_modes": ["ownership handoff without fencing"],
            "state_transition_failure_modes": [],
            "observability_failure_modes": [],
        },
        "cross_cutting_flags": ["recovery_integrity", "stale_state_handling", "operator_safety", "claim_integrity"],
    }
    task_profile = {
        "primary_task_type": "system_hardening",
        "task_families": ["scheduler_safety", "scheduler_hardening"],
        "secondary_tags": ["system_hardening"],
        "ambiguity_score": 0.2,
        "retrieval_bias": {
            "include": ["scheduler_core", "schedule_calculation", "lease_claims", "state_persistence", "validation", "audit_receipts"],
            "exclude": ["docs", "reports", "runtime"],
        },
        "cross_cutting_flags": ["recovery_integrity", "stale_state_handling", "operator_safety", "claim_integrity"],
    }

    search_plan = build_search_plan(job_contract, task_profile, max_candidates=60, intake_assessment=intake_assessment)["search_plan"]
    query_texts = {entry["query"] for entry in search_plan["queries"]}
    assert "scheduler_pipeline_healer.py" in query_texts
    assert "stale claim" in query_texts
    assert "reconcile" in query_texts
    assert "orphan" in query_texts

    result_obj = {
        "job_contract": job_contract,
        "intake_assessment": intake_assessment,
        "task_profile": task_profile,
        "search_plan": search_plan,
        "extracted_terms": {"file_hints": []},
    }

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=_base_config())["retrieval"]
    ranked = rank_context({**result_obj, "retrieval": retrieval}, _base_config())["ranked_context"]
    selected_targets = [entry["path"] for entry in ranked["implementation_targets"]]

    assert "infra/bin/scheduler_pipeline_healer.py" in selected_targets[:4]
    assert "infra/bin/scheduler_analyze_job.py" not in selected_targets[:4]
    assert {
        "infra/bin/scheduler_schedule_job.py",
        "infra/bin/scheduler_recovery_assistant.py",
        "infra/bin/scheduler_pipeline_healer.py",
        "infra/bin/scheduler_verify_job.py",
    }.issubset(set(selected_targets[:4]))


def test_workflow_recovery_prompt_does_not_drift_into_pipeline_code(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / "infra/pipeline/stages").mkdir(parents=True)
    (repo_root / "infra/bin/scheduler_watchdog.py").write_text(
        "def expire_stale_claims():\n"
        "    reclaim_lease()\n"
        "    mark_worker_stale()\n"
        "    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/bin/scheduler_recovery_assistant.py").write_text(
        "def reconcile_failed_run():\n"
        "    repair_orphaned_run()\n"
        "    update_job_state()\n"
        "    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/bin/scheduler_pipeline_healer.py").write_text(
        "def heal_scheduler_pipeline():\n"
        "    reconcile_orphaned_run()\n"
        "    repair_stale_claim()\n"
        "    update_job_state()\n"
        "    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/bin/scheduler_schedule_job.py").write_text(
        "def schedule_due_jobs():\n"
        "    stale_claim_guard()\n"
        "    reclaim_lease()\n"
        "    update_job_state()\n"
        "    dispatch_ready_job()\n"
        "    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/pipeline/stages/search_plan.py").write_text(
        "RECOVERY_TEXT = 'stale claim recovery orphan reconciliation watchdog healer repair'\n",
        encoding="utf-8",
    )
    (repo_root / "infra/pipeline/planning.py").write_text(
        "PIPELINE_TEXT = 'scheduler orphan reconcile recovery planner'\n",
        encoding="utf-8",
    )

    prompt = "add stale claim recovery and orphan reconciliation to the scheduler"
    job_contract = {
        "repo_root": str(repo_root),
        "title": prompt,
        "description": prompt,
        "objective": prompt,
        "allowed_paths": [],
        "forbidden_paths": [],
        "issue_context": {},
        "constraints": [],
        "acceptance_criteria": [],
    }
    intake_assessment = {
        "failure_taxonomy": {
            "operator_failure_modes": [],
            "recovery_failure_modes": ["stale claim not reclaimed", "orphaned run not reconciled"],
            "concurrency_failure_modes": ["ownership handoff without fencing"],
            "state_transition_failure_modes": [],
            "observability_failure_modes": [],
        },
        "cross_cutting_flags": ["recovery_integrity", "stale_state_handling", "claim_integrity"],
    }
    task_profile = {
        "primary_task_type": "feature",
        "task_families": ["reconciliation_recovery", "workflow_recovery"],
        "secondary_tags": ["workflow_recovery"],
        "ambiguity_score": 0.2,
        "retrieval_bias": {
            "include": ["recovery_mechanisms", "lease_claims", "queue_dispatch", "state_persistence", "audit_receipts"],
            "exclude": ["docs", "reports", "runtime"],
        },
        "cross_cutting_flags": ["recovery_integrity", "stale_state_handling", "claim_integrity"],
    }

    search_plan = build_search_plan(job_contract, task_profile, max_candidates=60, intake_assessment=intake_assessment)["search_plan"]
    retrieval = build_retrieval(
        {
            "job_contract": job_contract,
            "intake_assessment": intake_assessment,
            "task_profile": task_profile,
            "search_plan": search_plan,
            "extracted_terms": {"file_hints": []},
        },
        repo_root=repo_root,
        rg_path="rg",
        timeout_seconds=30,
        config=_base_config(),
    )["retrieval"]
    ranked = rank_context(
        {
            "job_contract": job_contract,
            "intake_assessment": intake_assessment,
            "task_profile": task_profile,
            "search_plan": search_plan,
            "retrieval": retrieval,
        },
        _base_config(),
    )["ranked_context"]

    selected_targets = [entry["path"] for entry in ranked["implementation_targets"][:4]]
    assert set(selected_targets) == {
        "infra/bin/scheduler_watchdog.py",
        "infra/bin/scheduler_recovery_assistant.py",
        "infra/bin/scheduler_pipeline_healer.py",
        "infra/bin/scheduler_schedule_job.py",
    }
    assert "infra/pipeline/stages/search_plan.py" not in selected_targets
    assert "infra/pipeline/planning.py" not in selected_targets


def test_search_plan_memory_prompt_prefers_lab_assistant_surfaces_and_allows_docs_reference(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = _lab_assistant_memory_prompt()
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    search_plan = build_search_plan(prompt, task_profile, max_candidates=120, intake_assessment=intake)["search_plan"]

    query_texts = {entry["query"] for entry in search_plan["queries"]}

    assert "server_lab_assistant_execution_truth_ownership.py" in query_texts
    assert "server_lab_assistant_execution_reconciliation.py" in query_texts
    assert "server_lab_assistant_task_register.py" in query_texts
    assert "server_lab_assistant_turns.py" in query_texts
    assert "server_lab_assistant_recovery.py" in query_texts
    assert "lab_assistant_execution_truth_ownership.md" in query_texts
    assert "scheduler_pipeline_healer.py" not in query_texts
    assert "Acceptance" not in query_texts
    assert "Chapter" not in query_texts
    assert "Title" not in query_texts
    assert "Context" not in query_texts
    assert "docs/" not in search_plan["negative_targets"]
    assert "reports/" not in search_plan["negative_targets"]
    assert "truth_ownership" in search_plan["subsystem_hypotheses"]
    assert "execution_reconciliation" in search_plan["subsystem_hypotheses"]
    assert "task_register" in search_plan["subsystem_hypotheses"]
    assert "docs_reference_allowed_for_lab_assistant_memory=true" in search_plan["notes"]
    assert "truth ownership" in search_plan["semantic_query_text"]
    assert "ledger" in search_plan["semantic_query_text"]


def test_memory_prompt_retrieval_and_ranking_prefer_owner_modules_over_hubs(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    prompt = _lab_assistant_memory_prompt()
    files = {
        "infra/webui/server_lab_assistant_execution_truth_ownership.py": "truth ownership provenance freshness canonical evidence runtime truth writeback boundary task register",
        "infra/webui/server_lab_assistant_execution_reconciliation.py": "execution reconciliation contradiction correction history ledger evidence provenance task register",
        "infra/webui/server_lab_assistant_recovery.py": "recovery restart replay-safe memory ingest provenance receipts task register",
        "infra/webui/server_lab_assistant_turns.py": "turn transcript artifact report evidence source links provenance freshness",
        "infra/webui/server_lab_assistant_task_register.py": "task register task register cross-project reuse boundaries recommendation support source truth ownership",
        "infra/webui/server_lab_assistant_writeback.py": "writeback projection canonical runtime truth memory summary",
        "infra/webui/server_ledger_actions.py": "ledger evidence provenance correction lineage auditability",
        "infra/webui/ledger/ledger_storage.py": "ledger storage evidence provenance freshness correction history",
        "infra/webui/server_routes_and_main.py": "route registry for lab assistant memory truth ownership reconciliation transcript ledger writeback correction",
        "infra/controlplane/scheduled_jobs.py": "scheduled jobs cron retry runtime heartbeat watchdog",
        "infra/pipeline/stages/search_plan.py": "pipeline planning memory provenance task register truth ownership reconciliation ledger transcript correction",
        "docs/lab_assistant_execution_truth_ownership.md": "truth ownership contract for research memory and evidence provenance",
        "docs/lab_assistant_task_register.md": "task register contract and cross-project boundaries",
        "docs/reports/research_memory.md": "research memory parent report provenance correction history",
    }
    for rel, content in files.items():
        path = repo_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    search_plan = build_search_plan(prompt, task_profile, max_candidates=120, intake_assessment=intake)["search_plan"]
    config = _base_config()
    config["limits"].update({"max_primary_targets": 6, "max_selected_files": 10, "max_bundle_files": 10})
    result_obj = {
        "job_contract": {"repo_root": str(repo_root), "allowed_paths": [], "forbidden_paths": [], "issue_context": {}, **prompt},
        "task_profile": task_profile,
        "search_plan": search_plan,
        "extracted_terms": {"file_hints": []},
        "intake_assessment": intake,
    }

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=config)["retrieval"]
    candidate_by_path = {entry["path"]: entry for entry in retrieval["file_candidates"]}

    assert "task_register_flow" in candidate_by_path["infra/webui/server_lab_assistant_task_register.py"]["covers"]
    assert "truth_boundary" in candidate_by_path["infra/webui/server_lab_assistant_execution_truth_ownership.py"]["covers"]
    assert "reconciliation_path" in candidate_by_path["infra/webui/server_lab_assistant_execution_reconciliation.py"]["covers"]
    assert candidate_by_path["infra/webui/server_routes_and_main.py"]["superficial_match_penalty"] > 0
    assert candidate_by_path["infra/pipeline/stages/search_plan.py"]["superficial_match_penalty"] > 0
    assert "infra/controlplane/scheduled_jobs.py" not in candidate_by_path

    ranked = rank_context({**result_obj, "retrieval": retrieval}, config)["ranked_context"]
    implementation_paths = [entry["path"] for entry in ranked["implementation_targets"]]
    reference_paths = [entry["path"] for entry in ranked["reference_context"]]
    selected_paths = [entry["path"] for entry in ranked["selected_files"]]

    assert "infra/webui/server_lab_assistant_execution_truth_ownership.py" in implementation_paths
    assert "infra/webui/server_lab_assistant_execution_reconciliation.py" in implementation_paths
    assert "infra/webui/server_lab_assistant_turns.py" in implementation_paths
    assert "infra/webui/server_routes_and_main.py" not in implementation_paths
    assert "infra/pipeline/stages/search_plan.py" not in implementation_paths
    assert "infra/pipeline/stages/search_plan.py" not in selected_paths
    assert "docs/lab_assistant_execution_truth_ownership.md" in reference_paths
    assert "docs/lab_assistant_task_register.md" in reference_paths





def test_narrow_ui_prompt_suppresses_failure_drift_and_keeps_bundle_tight(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    files = {
        "infra/webui/static/agents.js": """function renderAgentsPage() {
  const startButtonWidth = clampMobileButtonWidth('start-button');
  const checkboxLabel = 'Show archived agents';
  return startButtonWidth + checkboxLabel.length;
}
""",
        "infra/webui/server_agents.py": """def render_agents_page():
    validate_agents_filter()
    return 'agents page mobile checkbox start button'
""",
        "infra/webui/server_routes_and_main.py": """def register_routes():
    return 'route registry main agents page mobile start button width validation operator control'
""",
        "infra/webui/server_agents_orchestrator.py": """def orchestrate_agents():
    return 'agent orchestrator panel page mobile start button validation state transition operator flow'
""",
        "infra/controlplane/scheduled_jobs.py": """def run_scheduled_jobs():
    return 'scheduled jobs start button operator manual resume validation width mobile page'
""",
        "tools/benchmarks/agent_benchmark.py": """def benchmark_agents():
    return 'benchmark mobile button width page for agents'
""",
    }
    for rel, content in files.items():
        target = repo_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    prompt = {
        "title": "Buttons are too wide on the agents page on mobile",
        "description": "Fix the start button width on the agents page on mobile without changing scheduler behavior.",
        "objective": "Keep the mobile button width local to the agents page and preserve the existing checkbox behavior.",
        "constraints": [],
        "acceptance_criteria": [],
        "issue_context": {"changed_files_hint": ["infra/webui/static/agents.js"]},
        "task_type_hint": "unknown",
    }
    task_profile = {
        "primary_task_type": "bug_fix",
        "secondary_tags": ["ui_bug"],
        "task_families": ["multi_agent_orchestration"],
        "replace_spec": None,
        "expected_edit_scope": "few_files",
        "retrieval_bias": {"include": [], "exclude": ["docs", "reports", "runtime"]},
        "cross_cutting_flags": [],
    }
    config = _base_config()

    search_plan = build_search_plan(prompt, task_profile, max_candidates=80)["search_plan"]
    query_texts = {entry["query"] for entry in search_plan["queries"]}

    assert "watchdog" not in query_texts
    assert "manual action" not in query_texts
    assert "ledger record" not in query_texts
    assert "truth ownership" not in query_texts
    assert "narrow_task_suppression=true" in search_plan["notes"]
    assert search_plan["max_candidates"] == 60

    result_obj = {
        "job_contract": {"repo_root": str(repo_root), "allowed_paths": [], "forbidden_paths": [], **prompt},
        "task_profile": task_profile,
        "search_plan": search_plan,
        "extracted_terms": {"file_hints": ["infra/webui/static/agents.js"]},
        "intake_assessment": {"failure_taxonomy": {}},
    }
    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=config)["retrieval"]
    candidate_by_path = {entry["path"]: entry for entry in retrieval["file_candidates"]}
    assert candidate_by_path["infra/webui/server_routes_and_main.py"]["superficial_match_penalty"] > 0
    if "infra/controlplane/scheduled_jobs.py" in candidate_by_path:
        assert candidate_by_path["infra/controlplane/scheduled_jobs.py"]["superficial_match_penalty"] > 0

    ranked = rank_context({**result_obj, "retrieval": retrieval}, config)["ranked_context"]
    implementation_paths = [entry["path"] for entry in ranked["implementation_targets"]]
    selected_paths = [entry["path"] for entry in ranked["selected_files"]]

    assert implementation_paths[0] == "infra/webui/static/agents.js"
    assert "infra/webui/server_routes_and_main.py" not in implementation_paths
    assert "infra/controlplane/scheduled_jobs.py" not in implementation_paths
    assert "infra/webui/server_agents_orchestrator.py" not in implementation_paths
    assert len(selected_paths) <= 4

    bundle = bundle_context({**result_obj, "retrieval": retrieval, "ranked_context": ranked}, config)["context_bundle"]
    assert len(bundle["files"]) <= 4
    assert len(bundle["editable_targets"]) <= 2
    for entry in bundle["files"]:
        if "candidate_intent_lane" in entry:
            assert entry["candidate_intent_lane"] is None or str(entry["candidate_intent_lane"]).strip()
        if "context_intent_type" in entry:
            assert entry["context_intent_type"] is None or str(entry["context_intent_type"]).strip()


def test_explicit_core_hub_request_remains_retrievable(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    files = {
        "infra/webui/server_routes_and_main.py": """def register_builder_route():
    validate_builder_request()
    return 'builder endpoint route registration panel page width'
""",
        "infra/webui/static/agents.js": """function renderBuilderPage() { return 'builder page'; }
""",
        "infra/webui/server_agents_orchestrator.py": """def orchestrate_agents():
    return 'agent orchestration for builder page'
""",
    }
    for rel, content in files.items():
        target = repo_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    prompt = {
        "title": "Add one builder endpoint in server_routes_and_main.py",
        "description": "Update server_routes_and_main.py to register a single builder admin endpoint.",
        "objective": "Make the route live in server_routes_and_main.py without changing unrelated scheduler code.",
        "constraints": [],
        "acceptance_criteria": [],
        "issue_context": {"changed_files_hint": ["infra/webui/server_routes_and_main.py"]},
        "task_type_hint": "unknown",
    }
    task_profile = {
        "primary_task_type": "feature",
        "secondary_tags": [],
        "task_families": ["multi_agent_orchestration"],
        "replace_spec": None,
        "expected_edit_scope": "single_file",
        "retrieval_bias": {"include": [], "exclude": ["docs", "reports", "runtime"]},
        "cross_cutting_flags": [],
    }
    config = _base_config()

    search_plan = build_search_plan(prompt, task_profile, max_candidates=80)["search_plan"]
    result_obj = {
        "job_contract": {"repo_root": str(repo_root), "allowed_paths": [], "forbidden_paths": [], **prompt},
        "task_profile": task_profile,
        "search_plan": search_plan,
        "extracted_terms": {"file_hints": ["infra/webui/server_routes_and_main.py"]},
        "intake_assessment": {"failure_taxonomy": {}},
    }

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=config)["retrieval"]
    candidate_by_path = {entry["path"]: entry for entry in retrieval["file_candidates"]}
    assert candidate_by_path["infra/webui/server_routes_and_main.py"].get("hub_kind") == "webui_router_hub"
    assert candidate_by_path["infra/webui/server_routes_and_main.py"].get("explicit_path_request") is True

    ranked = rank_context({**result_obj, "retrieval": retrieval}, config)["ranked_context"]
    implementation_paths = [entry["path"] for entry in ranked["implementation_targets"]]

    assert implementation_paths[0] == "infra/webui/server_routes_and_main.py"



def test_precision_calibration_clear_scheduler_task_prefers_central_targets() -> None:
    prompt = "fix duplicate execution in scheduler"
    job_contract = {
        "title": prompt,
        "description": prompt,
        "objective": prompt,
        "issue_context": {},
        "constraints": [],
        "acceptance_criteria": [],
        "repo_root": str(Path(__file__).resolve().parents[2]),
    }
    task_profile = {
        "primary_task_type": "bug_fix",
        "secondary_tags": ["scheduler_hardening"],
        "task_families": ["scheduler_hardening"],
        "replace_spec": None,
        "retrieval_bias": {
            "include": ["scheduler_core", "lease_claims", "recovery_mechanisms"],
            "exclude": ["docs", "reports"],
        },
        "cross_cutting_flags": ["duplicate_suppression", "concurrency_control", "recovery_integrity", "operator_safety"],
        "ambiguity_score": 0.15,
    }
    search_plan = build_search_plan(job_contract, task_profile, max_candidates=80)["search_plan"]

    assert search_plan["confidence"]["score"] > 0.8
    assert any("same logical job more than once" in query for query in search_plan["conceptual_queries"])
    assert not any("illegal source-to-target transitions" in query for query in search_plan["conceptual_queries"])
    assert not any("logged with actor" in query for query in search_plan["conceptual_queries"])

    retrieval = {
        "file_candidates": [
            _candidate(
                "infra/controlplane/scheduler_watchdog.py",
                lexical=3.0,
                structural=2.0,
                graph=1.6,
                matched_terms=["scheduler", "duplicate", "recovery", "watchdog"],
                covers=["recovery_responsibility", "ownership_control", "dispatch_path", "state_mutation"],
            ),
            _candidate(
                "infra/controlplane/scheduler_schedule_job.py",
                lexical=2.8,
                structural=2.1,
                graph=1.5,
                matched_terms=["scheduler", "duplicate", "dispatch", "schedule"],
                covers=["dispatch_path", "validation_path", "state_mutation"],
            ),
            _candidate(
                "infra/controlplane/scheduler_verify_job.py",
                lexical=2.7,
                structural=2.0,
                graph=1.4,
                matched_terms=["scheduler", "duplicate", "validation"],
                covers=["dispatch_path", "validation_path"],
            ),
            _candidate(
                "infra/controlplane/scheduler_enable_disable_jobs.py",
                lexical=2.2,
                structural=1.4,
                graph=1.1,
                matched_terms=["operator", "pause", "resume", "scheduler"],
                covers=["operator_flow", "validation_path"],
            ),
            _candidate(
                "infra/webui/server_agents.py",
                lexical=3.1,
                structural=1.5,
                graph=0.9,
                matched_terms=["scheduler", "duplicate", "execution"],
                covers=["operator_flow"],
            ),
            _candidate(
                "infra/pipeline/knowledge/failure_taxonomy.yaml",
                lexical=3.0,
                structural=0.4,
                graph=0.5,
                matched_terms=["duplicate", "failure", "recovery"],
                covers=[],
            ),
        ]
    }
    ranked = rank_context(
        {
            "job_contract": job_contract,
            "task_profile": task_profile,
            "search_plan": search_plan,
            "intake_assessment": {"failure_taxonomy": {}},
            "retrieval": retrieval,
        },
        _base_config(),
    )["ranked_context"]
    implementation_paths = [entry["path"] for entry in ranked["implementation_targets"]]
    reference_paths = [entry["path"] for entry in ranked["reference_context"]]

    assert implementation_paths[:2] == [
        "infra/controlplane/scheduler_watchdog.py",
        "infra/controlplane/scheduler_schedule_job.py",
    ]
    assert "infra/webui/server_agents.py" not in implementation_paths
    assert "infra/pipeline/knowledge/failure_taxonomy.yaml" not in implementation_paths
    assert "infra/pipeline/knowledge/failure_taxonomy.yaml" in reference_paths



def test_precision_calibration_vague_prompt_keeps_low_confidence_and_drops_generic_concepts() -> None:
    prompt = "make it safer"
    job_contract = {
        "title": prompt,
        "description": prompt,
        "objective": prompt,
        "issue_context": {},
        "constraints": [],
        "acceptance_criteria": [],
    }
    task_profile = {
        "primary_task_type": "system_hardening",
        "secondary_tags": ["system_hardening"],
        "task_families": ["generic"],
        "replace_spec": None,
        "retrieval_bias": {"include": [], "exclude": ["docs", "reports"]},
        "cross_cutting_flags": [],
        "ambiguity_score": 0.85,
    }

    search_plan = build_search_plan(job_contract, task_profile, max_candidates=80)["search_plan"]

    assert search_plan["confidence"]["score"] < 0.6
    assert len(search_plan["conceptual_queries"]) <= 7
    assert "partial write recovery" in search_plan["conceptual_queries"]
    assert len(search_plan["subsystem_hypotheses"]) >= 4
    assert not any("illegal source-to-target transitions" in query for query in search_plan["conceptual_queries"])
    assert not any("logged with actor" in query for query in search_plan["conceptual_queries"])



def test_precision_calibration_mixed_restart_pause_task_balances_target_selection() -> None:
    prompt = "improve restart and operator pause behavior"
    job_contract = {
        "title": prompt,
        "description": prompt,
        "objective": prompt,
        "issue_context": {},
        "constraints": [],
        "acceptance_criteria": [],
        "repo_root": str(Path(__file__).resolve().parents[2]),
    }
    task_profile = {
        "primary_task_type": "system_hardening",
        "secondary_tags": ["scheduler_hardening"],
        "task_families": ["scheduler_hardening"],
        "replace_spec": None,
        "retrieval_bias": {
            "include": ["scheduler_core", "operator_entrypoints", "recovery_mechanisms"],
            "exclude": ["docs", "reports"],
        },
        "cross_cutting_flags": ["recovery_integrity", "operator_safety", "state_consistency"],
        "ambiguity_score": 0.25,
    }
    search_plan = build_search_plan(job_contract, task_profile, max_candidates=80)["search_plan"]

    assert search_plan["confidence"]["score"] > 0.8
    assert "operator_entrypoints" in search_plan["subsystem_hypotheses"]
    assert "recovery_mechanisms" in search_plan["subsystem_hypotheses"]
    assert any("manual control actions can race" in query for query in search_plan["conceptual_queries"])
    assert any("worker is live, stale, or recoverable" in query for query in search_plan["conceptual_queries"])

    retrieval = {
        "file_candidates": [
            _candidate(
                "infra/controlplane/scheduler_watchdog.py",
                lexical=3.0,
                structural=2.0,
                graph=1.6,
                matched_terms=["restart", "recovery", "watchdog"],
                covers=["recovery_responsibility", "ownership_control", "state_mutation"],
            ),
            _candidate(
                "infra/controlplane/scheduler_recovery_assistant.py",
                lexical=2.8,
                structural=1.9,
                graph=1.4,
                matched_terms=["restart", "reconcile", "recovery"],
                covers=["recovery_path", "state_mutation"],
            ),
            _candidate(
                "infra/controlplane/scheduler_enable_disable_jobs.py",
                lexical=2.7,
                structural=1.6,
                graph=1.3,
                matched_terms=["operator", "pause", "resume", "scheduler"],
                covers=["operator_flow", "validation_path"],
            ),
            _candidate(
                "infra/controlplane/scheduler_verify_job.py",
                lexical=2.5,
                structural=1.8,
                graph=1.2,
                matched_terms=["validation", "pause", "restart"],
                covers=["validation_path"],
            ),
            _candidate(
                "infra/webui/server_agents.py",
                lexical=3.0,
                structural=1.4,
                graph=0.9,
                matched_terms=["operator", "pause", "restart"],
                covers=["operator_flow"],
            ),
            _candidate(
                "infra/controlplane/scheduler_binding.py",
                lexical=2.6,
                structural=0.8,
                graph=0.6,
                matched_terms=["restart", "pause"],
                covers=[],
            ),
        ]
    }
    ranked = rank_context(
        {
            "job_contract": job_contract,
            "task_profile": task_profile,
            "search_plan": search_plan,
            "intake_assessment": {"failure_taxonomy": {}},
            "retrieval": retrieval,
        },
        _base_config(),
    )["ranked_context"]
    implementation_paths = [entry["path"] for entry in ranked["implementation_targets"]]

    assert "infra/controlplane/scheduler_watchdog.py" in implementation_paths
    assert "infra/controlplane/scheduler_recovery_assistant.py" in implementation_paths
    assert "infra/controlplane/scheduler_enable_disable_jobs.py" in implementation_paths
    assert "infra/webui/server_agents.py" not in implementation_paths
    assert "infra/controlplane/scheduler_binding.py" not in implementation_paths



def test_precision_calibration_knowledge_assets_remain_reference_only() -> None:
    prompt = "fix duplicate execution in scheduler"
    job_contract = {
        "title": prompt,
        "description": prompt,
        "objective": prompt,
        "issue_context": {},
        "constraints": [],
        "acceptance_criteria": [],
        "repo_root": str(Path(__file__).resolve().parents[2]),
    }
    task_profile = {
        "primary_task_type": "bug_fix",
        "secondary_tags": ["scheduler_hardening"],
        "task_families": ["scheduler_hardening"],
        "replace_spec": None,
        "retrieval_bias": {"include": ["scheduler_core", "recovery_mechanisms"], "exclude": ["docs", "reports"]},
        "cross_cutting_flags": ["duplicate_suppression", "recovery_integrity"],
        "ambiguity_score": 0.15,
    }
    search_plan = build_search_plan(job_contract, task_profile, max_candidates=80)["search_plan"]
    retrieval = {
        "file_candidates": [
            _candidate(
                "infra/controlplane/scheduler_watchdog.py",
                lexical=3.0,
                structural=2.0,
                graph=1.6,
                matched_terms=["scheduler", "duplicate", "recovery"],
                covers=["recovery_responsibility", "ownership_control", "state_mutation"],
            ),
            _candidate(
                "infra/controlplane/scheduler_schedule_job.py",
                lexical=2.7,
                structural=1.8,
                graph=1.4,
                matched_terms=["scheduler", "duplicate", "dispatch"],
                covers=["dispatch_path", "validation_path"],
            ),
            _candidate(
                "infra/pipeline/knowledge/failure_taxonomy.yaml",
                lexical=3.2,
                structural=0.5,
                graph=0.5,
                matched_terms=["duplicate", "failure", "recovery", "scheduler"],
                covers=[],
            ),
        ]
    }
    ranked = rank_context(
        {
            "job_contract": job_contract,
            "task_profile": task_profile,
            "search_plan": search_plan,
            "intake_assessment": {"failure_taxonomy": {}},
            "retrieval": retrieval,
        },
        _base_config(),
    )["ranked_context"]
    implementation_paths = [entry["path"] for entry in ranked["implementation_targets"]]
    reference_paths = [entry["path"] for entry in ranked["reference_context"]]

    assert "infra/pipeline/knowledge/failure_taxonomy.yaml" not in implementation_paths
    assert "infra/pipeline/knowledge/failure_taxonomy.yaml" in reference_paths


def test_retrieval_batches_ripgrep_and_prefilters_large_candidate_pool(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/controlplane").mkdir(parents=True)

    central = repo_root / "infra/controlplane/scheduler_retry_manager.py"
    central.write_text(
        "def schedule_due_jobs():\n"
        "    scheduler = True\n"
        "    retry = True\n"
        "    logic = True\n"
        "    dispatch = True\n"
        "    recovery = True\n"
        "    heartbeat = True\n"
        "    checkpoint = True\n"
        "    duplicate = True\n"
        "    watchdog = True\n",
        encoding="utf-8",
    )
    for idx in range(140):
        noise = repo_root / f"infra/controlplane/noise_{idx:03d}.py"
        noise.write_text(
            "scheduler retry logic dispatch recovery state\n"
            if idx % 2 == 0
            else "scheduler retry logic heartbeat checkpoint duplicate\n",
            encoding="utf-8",
        )

    queries = [
        {"query": "scheduler", "query_type": "literal", "weight": 0.9},
        {"query": "retry", "query_type": "literal", "weight": 0.9},
        {"query": "logic", "query_type": "literal", "weight": 0.7},
        {"query": "dispatch", "query_type": "literal", "weight": 0.7},
        {"query": "recovery", "query_type": "literal", "weight": 0.7},
        {"query": "heartbeat", "query_type": "literal", "weight": 0.7},
        {"query": "checkpoint", "query_type": "literal", "weight": 0.7},
        {"query": "duplicate", "query_type": "literal", "weight": 0.7},
        {"query": "watchdog", "query_type": "literal", "weight": 0.7},
        {"query": "schedule_due_jobs", "query_type": "symbol", "weight": 1.1},
        {"query": "scheduler_retry_manager.py", "query_type": "filename", "weight": 1.0},
    ]
    query_coverage_map = {
        "scheduler": {"subsystems": ["scheduler_core"], "covers": ["system_failure_modes"]},
        "retry": {"subsystems": ["retry_backoff"], "covers": ["recovery_failure_modes"]},
        "recovery": {"subsystems": ["recovery_mechanisms"], "covers": ["recovery_failure_modes"]},
        "heartbeat": {"subsystems": ["heartbeat_liveness"], "covers": ["concurrency_failure_modes"]},
        "checkpoint": {"subsystems": ["state_persistence"], "covers": ["system_failure_modes", "persistence_failure"]},
        "duplicate": {"subsystems": ["scheduler_core"], "covers": ["concurrency_failure_modes"]},
        "schedule_due_jobs": {"subsystems": ["scheduler_core"], "covers": []},
        "scheduler_retry_manager.py": {"subsystems": ["scheduler_core"], "covers": []},
    }
    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "allowed_paths": [],
            "forbidden_paths": [],
            "issue_context": {},
            "title": "fix scheduler retry logic",
            "description": "fix scheduler retry logic",
            "objective": "fix scheduler retry logic",
            "constraints": [],
            "acceptance_criteria": [],
        },
        "task_profile": {
            "primary_task_type": "bug_fix",
            "task_families": ["scheduler_hardening"],
            "secondary_tags": ["scheduler_hardening"],
            "retrieval_bias": {"include": ["scheduler_core", "retry_backoff", "recovery_mechanisms"], "exclude": ["docs", "reports"]},
            "cross_cutting_flags": ["recovery_integrity", "duplicate_suppression"],
        },
        "search_plan": {
            "queries": queries,
            "lexical_queries": [entry["query"] for entry in queries],
            "conceptual_queries": [],
            "negative_targets": [],
            "subsystem_hypotheses": ["scheduler_core", "retry_backoff", "recovery_mechanisms"],
            "query_coverage_map": query_coverage_map,
            "strategies": ["ripgrep_literal", "filename_glob", "ast_grep"],
            "max_candidates": 20,
            "notes": [],
            "semantic_query_text": "scheduler retry logic",
            "confidence": {"score": 0.86},
        },
        "intake_assessment": {"failure_taxonomy": {}},
        "extracted_terms": {"file_hints": []},
    }

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=_base_config())["retrieval"]
    stats = retrieval["retrieval_stats"]
    paths = [entry["path"] for entry in retrieval["file_candidates"]]
    non_filename_query_count = len([entry for entry in queries if entry["query_type"] != "filename"])

    assert stats["rg_batches"] < non_filename_query_count
    assert stats["pass2_candidate_count"] < stats["prefilter_candidate_count"]
    assert "infra/controlplane/scheduler_retry_manager.py" in paths


def test_owner_collapse_prefers_orchestration_owner_and_paired_test() -> None:
    result_obj = {
        "job_contract": {
            "repo_root": ".",
            "title": "Crash Safe Idempotent Orchestration Turn Checkpointing",
            "description": "ensure idempotent checkpointing and prevent duplicate execution after restart",
            "objective": "persist turn execution checkpointing and prevent duplicate replay after restart",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "bugfix",
            "task_families": ["orchestration_checkpointing"],
            "secondary_tags": [],
            "ambiguity_score": 0.2,
        },
        "search_plan": {"subsystem_hypotheses": [], "query_coverage_map": {}},
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "infra/webui/server_agents_orchestrator.py",
                    lexical=4.0,
                    structural=5.0,
                    graph=2.0,
                    matched_terms=["checkpoint", "turn", "execution", "restart"],
                    covers=["state_mutation", "recovery_responsibility", "validation_path"],
                ),
                _candidate(
                    "infra/webui/server_agents.py",
                    lexical=3.0,
                    structural=4.0,
                    graph=1.0,
                    matched_terms=["checkpoint", "restart"],
                    covers=["recovery_path", "dispatch_path"],
                ),
                _candidate(
                    "infra/webui/server_routes_and_main.py",
                    lexical=2.0,
                    structural=4.0,
                    graph=1.0,
                    matched_terms=["checkpoint"],
                    covers=["operator_flow"],
                ),
                _candidate(
                    "tests/control_plane/webui/test_webui_agents_orchestration.py",
                    lexical=2.0,
                    structural=3.0,
                    graph=1.0,
                    matched_terms=["checkpoint", "restart"],
                    covers=["validation_path"],
                ),
                _candidate(
                    "infra/webui/static/agents.js",
                    lexical=2.0,
                    structural=2.0,
                    graph=0.0,
                    matched_terms=["checkpoint"],
                    covers=[],
                ),
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())["ranked_context"]
    implementation_targets = [entry["path"] for entry in ranked["implementation_targets"]]

    assert implementation_targets == [
        "infra/webui/server_agents_orchestrator.py",
        "tests/control_plane/webui/test_webui_agents_orchestration.py",
    ]
    assert "infra/webui/server_agents.py" not in implementation_targets
    assert "infra/webui/server_routes_and_main.py" not in implementation_targets
    assert "infra/webui/static/agents.js" not in implementation_targets



def test_owner_collapse_prefers_ui_projection_owner_and_demotes_siblings() -> None:
    result_obj = {
        "job_contract": {
            "repo_root": ".",
            "title": "UI Projection, Staleness, Degraded, and Unreachable State Contract",
            "description": "ensure UI shows degraded vs unreachable states correctly and stale cached truth is marked honestly",
            "objective": "fix UI snapshot staleness and honest degraded or unreachable status projection",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "bugfix",
            "task_families": ["ui_truth_projection"],
            "secondary_tags": [],
            "ambiguity_score": 0.2,
        },
        "search_plan": {"subsystem_hypotheses": [], "query_coverage_map": {}},
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "infra/webui/server_lab_assistant_operator_snapshot.py",
                    lexical=4.0,
                    structural=5.0,
                    graph=1.0,
                    matched_terms=["snapshot", "degraded", "unreachable", "stale"],
                    covers=["validation_path", "state_mutation"],
                ),
                _candidate(
                    "infra/webui/server_lab_assistant_delegation_bindings.py",
                    lexical=3.0,
                    structural=4.0,
                    graph=1.0,
                    matched_terms=["status", "projection"],
                    covers=["operator_flow"],
                ),
                _candidate(
                    "infra/controlplane/scheduled_jobs.py",
                    lexical=2.0,
                    structural=4.0,
                    graph=1.0,
                    matched_terms=["status"],
                    covers=["dispatch_path"],
                ),
                _candidate(
                    "tests/control_plane/webui/test_lab_assistant_operator_snapshot.py",
                    lexical=2.0,
                    structural=3.0,
                    graph=1.0,
                    matched_terms=["snapshot", "unreachable"],
                    covers=["validation_path"],
                ),
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())["ranked_context"]
    implementation_targets = [entry["path"] for entry in ranked["implementation_targets"]]

    assert implementation_targets == [
        "infra/webui/server_lab_assistant_operator_snapshot.py",
        "tests/control_plane/webui/test_lab_assistant_operator_snapshot.py",
    ]
    assert "infra/webui/server_lab_assistant_delegation_bindings.py" not in implementation_targets
    assert "infra/controlplane/scheduled_jobs.py" not in implementation_targets



def test_retrieval_ranking_validation_only_prefers_pipeline_owner_and_test(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/pipeline").mkdir(parents=True)
    (repo_root / "tests/pipeline").mkdir(parents=True)
    (repo_root / "infra/pipeline/planning.py").write_text("def rank_evidence():\n    return 1\n", encoding="utf-8")
    (repo_root / "tests/pipeline/test_planning_flow.py").write_text("def test_rank_evidence():\n    assert True\n", encoding="utf-8")

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "title": "Cross-Project Retrieval Ranking, Freshness, and Evidence-First Answer Assembly Contract",
            "description": "improve retrieval ranking with freshness and evidence prioritization and explicit missing evidence handling",
            "objective": "rank direct evidence above stale memory and preserve applicability boundaries in answer assembly",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "bugfix",
            "task_families": ["retrieval_ranking_contract"],
            "secondary_tags": [],
            "ambiguity_score": 0.2,
        },
        "search_plan": {"subsystem_hypotheses": [], "query_coverage_map": {}},
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "infra/pipeline/planning.py",
                    lexical=4.0,
                    structural=5.0,
                    graph=2.0,
                    matched_terms=["retrieval", "ranking", "freshness", "evidence"],
                    covers=["validation_path", "truth_boundary"],
                ),
                _candidate(
                    "tests/pipeline/test_planning_flow.py",
                    lexical=2.0,
                    structural=3.0,
                    graph=1.0,
                    matched_terms=["ranking", "freshness"],
                    covers=["validation_path"],
                ),
                _candidate(
                    "infra/webui/server_lab_assistant_task_register.py",
                    lexical=2.0,
                    structural=4.0,
                    graph=1.0,
                    matched_terms=["memory", "evidence"],
                    covers=["task_register_flow"],
                ),
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())
    implementation_targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]]
    assert implementation_targets == [
        "infra/pipeline/planning.py",
        "tests/pipeline/test_planning_flow.py",
    ]

    bundled = bundle_context({**result_obj, **ranked}, _base_config())
    patch_plan, warnings = maybe_model_patch_plan({**result_obj, **ranked, **bundled}, _base_config())

    assert warnings
    assert patch_plan["plan_status"] == "VALIDATION_ONLY"
    assert patch_plan["plan_outcome"] == "VALIDATION_ONLY"
    assert [entry["path"] for entry in patch_plan["modify_files"]] == ["tests/pipeline/test_planning_flow.py"]
    assert patch_plan["required_tests"]


def test_retrieval_ranking_owner_beats_runtime_stage_pressure(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "infra/pipeline/stages").mkdir(parents=True)
    (repo_root / "tests/pipeline").mkdir(parents=True)
    (repo_root / "infra/pipeline/planning.py").write_text("def rank_evidence():\n    return 1\n", encoding="utf-8")
    (repo_root / "infra/pipeline/stages/search_plan.py").write_text("def plan_queries():\n    return []\n", encoding="utf-8")
    (repo_root / "infra/pipeline/stages/repo_retriever.py").write_text("def retrieve_candidates():\n    return []\n", encoding="utf-8")
    (repo_root / "tests/pipeline/test_planning_flow.py").write_text("def test_rank_evidence():\n    assert True\n", encoding="utf-8")

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "title": "Cross-Project Retrieval Ranking, Freshness, and Evidence-First Answer Assembly Contract",
            "description": "improve retrieval ranking, freshness, applicability boundaries, and explicit missing-evidence disclosure",
            "objective": "rank direct evidence above stale memory and preserve applicability boundaries in answer assembly",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "intake_assessment": {
            "active_failure_categories": [
                "operator_failure_modes",
                "system_failure_modes",
                "recovery_failure_modes",
            ],
            "active_invariant_buckets": [
                "ranking_evidence",
                "validation",
                "recovery",
                "persistence",
            ],
        },
        "task_profile": {
            "primary_task_type": "retrieval_ranking_contract",
            "task_families": ["retrieval_ranking_contract"],
            "secondary_tags": ["evidence_first", "freshness", "applicability_boundaries"],
            "cross_cutting_flags": [
                "observability",
                "validation_rigor",
                "auditability",
                "stale_state_handling",
                "state_consistency",
            ],
            "ambiguity_score": 0.37,
        },
        "search_plan": {
            "subsystem_hypotheses": [
                "knowledge_retrieval",
                "validation",
                "state_persistence",
                "ledger_evidence",
                "checkpoint_resume",
                "queue_dispatch",
            ],
            "query_coverage_map": {},
        },
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "infra/pipeline/planning.py",
                    lexical=4.6,
                    structural=5.2,
                    graph=2.2,
                    matched_terms=["retrieval", "ranking", "freshness", "evidence", "applicability", "answer"],
                    covers=["validation_path", "truth_boundary"],
                    matched_symbols=["rank_context", "bundle_context"],
                ),
                _candidate(
                    "infra/pipeline/stages/repo_retriever.py",
                    lexical=5.0,
                    structural=5.4,
                    graph=2.4,
                    matched_terms=["retrieval", "evidence", "freshness", "applicability", "answer"],
                    covers=["recovery_responsibility", "operator_flow", "validation_path", "state_mutation"],
                ),
                _candidate(
                    "infra/pipeline/stages/search_plan.py",
                    lexical=4.8,
                    structural=5.0,
                    graph=2.1,
                    matched_terms=["retrieval", "ranking", "freshness", "applicability"],
                    covers=["validation_path", "operator_flow"],
                ),
                _candidate(
                    "tests/pipeline/test_planning_flow.py",
                    lexical=3.0,
                    structural=3.4,
                    graph=1.1,
                    matched_terms=["ranking", "freshness", "evidence"],
                    covers=["validation_path"],
                ),
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())["ranked_context"]
    implementation_targets = [entry["path"] for entry in ranked["implementation_targets"]]
    reference_context = [entry["path"] for entry in ranked["reference_context"]]
    ranked_by_path = {entry["path"]: entry for entry in ranked["ranked_files"]}

    assert implementation_targets == [
        "infra/pipeline/planning.py",
        "tests/pipeline/test_planning_flow.py",
    ]
    assert "infra/pipeline/stages/repo_retriever.py" in reference_context
    assert "infra/pipeline/stages/search_plan.py" in reference_context
    assert ranked_by_path["infra/pipeline/planning.py"]["targetability_score"] > ranked_by_path["infra/pipeline/stages/repo_retriever.py"]["targetability_score"]
    assert ranked_by_path["infra/pipeline/planning.py"]["ownership_role"] == "core_owner"


def test_retrieval_owner_evidence_can_beat_planning_path_fallback(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/pipeline").mkdir(parents=True)
    (repo_root / "tests/pipeline").mkdir(parents=True)
    (repo_root / "infra/pipeline/planning.py").write_text("def rank_evidence():\n    return 1\n", encoding="utf-8")
    (repo_root / "infra/pipeline/evidence_owner.py").write_text(
        "def score_retrieval_candidate():\n    choose_evidence_first_source()\n    enforce_applicability_boundary_in_answer()\n    return 1\n",
        encoding="utf-8",
    )
    (repo_root / "tests/pipeline/test_planning_flow.py").write_text("def test_rank_evidence():\n    assert True\n", encoding="utf-8")

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "title": "Cross-Project Retrieval Ranking, Freshness, and Evidence-First Answer Assembly Contract",
            "description": "improve retrieval ranking with explicit owner evidence and keep adjacent stages as references",
            "objective": "rank direct evidence above stale memory and preserve applicability boundaries in answer assembly",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "retrieval_ranking_contract",
            "task_families": ["retrieval_ranking_contract"],
            "secondary_tags": ["evidence_first", "freshness", "applicability_boundaries"],
            "cross_cutting_flags": ["validation_rigor", "stale_state_handling"],
            "ambiguity_score": 0.24,
        },
        "search_plan": {
            "subsystem_hypotheses": ["retrieval_ranker", "answer_assembly", "validation"],
            "query_coverage_map": {},
        },
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "infra/pipeline/planning.py",
                    lexical=4.6,
                    structural=5.2,
                    graph=2.2,
                    matched_terms=["retrieval", "ranking", "freshness", "evidence"],
                    covers=["validation_path"],
                    matched_symbols=["rank_context"],
                ),
                _candidate(
                    "infra/pipeline/evidence_owner.py",
                    lexical=4.3,
                    structural=4.8,
                    graph=2.1,
                    matched_terms=["retrieval", "ranking", "evidence", "applicability", "answer"],
                    covers=["retrieval_owner", "validation_path"],
                    matched_symbols=["score_retrieval_candidate", "choose_evidence_first_source", "enforce_applicability_boundary_in_answer"],
                ),
                _candidate(
                    "tests/pipeline/test_planning_flow.py",
                    lexical=2.6,
                    structural=3.1,
                    graph=1.0,
                    matched_terms=["ranking", "evidence"],
                    covers=["validation_path"],
                ),
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())["ranked_context"]
    implementation_targets = [entry["path"] for entry in ranked["implementation_targets"]]
    ranked_by_path = {entry["path"]: entry for entry in ranked["ranked_files"]}

    assert implementation_targets == [
        "infra/pipeline/evidence_owner.py",
        "tests/pipeline/test_planning_flow.py",
    ]
    assert ranked_by_path["infra/pipeline/evidence_owner.py"]["ownership_role"] == "core_owner"
    assert ranked_by_path["infra/pipeline/evidence_owner.py"]["targetability_score"] > ranked_by_path["infra/pipeline/planning.py"]["targetability_score"]


def test_patch_plan_emits_tests_required_for_dominant_owner_without_adjacent_test(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/webui").mkdir(parents=True)
    (repo_root / "infra/webui/server_agents_orchestrator.py").write_text(
        "def checkpoint_turn():\n    return True\n",
        encoding="utf-8",
    )

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "title": "Crash Safe Idempotent Orchestration Turn Checkpointing",
            "description": "ensure idempotent checkpointing and prevent duplicate execution after restart",
            "objective": "persist turn execution checkpointing and prevent duplicate replay after restart",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "bugfix",
            "task_families": ["orchestration_checkpointing"],
            "secondary_tags": [],
            "ambiguity_score": 0.2,
        },
        "search_plan": {"subsystem_hypotheses": [], "query_coverage_map": {}},
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "infra/webui/server_agents_orchestrator.py",
                    lexical=4.0,
                    structural=5.0,
                    graph=2.0,
                    matched_terms=["checkpoint", "turn", "execution", "restart"],
                    covers=["state_mutation", "recovery_responsibility", "validation_path"],
                )
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())
    bundled = bundle_context({**result_obj, **ranked}, _base_config())
    patch_plan, warnings = maybe_model_patch_plan({**result_obj, **ranked, **bundled}, _base_config())

    assert warnings
    assert patch_plan["plan_status"] == "TESTS_REQUIRED"
    assert patch_plan["plan_outcome"] == "TESTS_REQUIRED"
    assert patch_plan["modify_files"] == []
    assert patch_plan["required_tests"]
    assert any("regression" in item.lower() or "checkpoint" in item.lower() or "replay" in item.lower() for item in patch_plan["required_tests"])



def test_explicit_bare_filename_hint_preserves_single_file_edit_target_for_tiny_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tests").mkdir(parents=True)
    (repo_root / "app.py").write_text(
        "def normalize_text(s):\n    return s.strip()\n",
        encoding="utf-8",
    )
    (repo_root / "tests/test_app.py").write_text(
        "from app import normalize_text\n\ndef test_empty_input_returns_empty_string():\n    assert normalize_text(\"\") == \"\"\n",
        encoding="utf-8",
    )

    prompt = {
        "title": "Fix the bug in app.py so empty input returns an empty string, and update tests if needed.",
        "description": "",
        "objective": "",
        "task_type_hint": "unknown",
        "constraints": [],
        "acceptance_criteria": [],
        "issue_context": {},
    }
    config = _base_config()
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    stage3 = build_search_plan(prompt, task_profile, max_candidates=100, intake_assessment=intake)
    search_plan = stage3["search_plan"]
    result_obj = {
        "job_contract": {**prompt, "repo_root": str(repo_root), "allowed_paths": [], "forbidden_paths": []},
        "intake_assessment": intake,
        "task_profile": task_profile,
        "search_plan": search_plan,
        "extracted_terms": stage3["extracted_terms"],
    }
    result_obj["retrieval"] = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=config)["retrieval"]

    ranked = rank_context(result_obj, config=config)
    assert [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]] == ["app.py"]

    bundled = bundle_context({**result_obj, **ranked}, config=config)
    assert [entry["path"] for entry in bundled["context_bundle"]["editable_targets"]] == ["app.py"]

    patch_plan, warnings = maybe_model_patch_plan({**result_obj, **ranked, **bundled}, config=config)

    assert warnings
    assert patch_plan["plan_status"] == "READY_FOR_REVIEW"
    assert patch_plan["plan_outcome"] == "READY_FOR_REVIEW"
    assert [entry["path"] for entry in patch_plan["modify_files"]] == ["app.py"]


def test_repo_retriever_preserves_stage3_extracted_terms_for_downstream_ranking(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("def normalize_text(s):\n    return s.strip()\n", encoding="utf-8")

    prompt = {
        "title": "Fix the bug in app.py so empty input returns an empty string.",
        "description": "",
        "objective": "",
        "task_type_hint": "unknown",
        "constraints": [],
        "acceptance_criteria": [],
        "issue_context": {},
    }
    config = _base_config()
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    stage3 = build_search_plan(prompt, task_profile, max_candidates=100, intake_assessment=intake)
    result_obj = {
        "job_contract": {**prompt, "repo_root": str(repo_root), "allowed_paths": [], "forbidden_paths": []},
        "intake_assessment": intake,
        "task_profile": task_profile,
        "search_plan": stage3["search_plan"],
        "extracted_terms": stage3["extracted_terms"],
    }

    stage4 = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=config)

    assert "app.py" in list(stage4["extracted_terms"].get("file_hints") or [])


def test_repo_retriever_skips_unparseable_ripgrep_line_numbers(monkeypatch: object, tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    def fake_run_subprocess(args, cwd, timeout_seconds):
        return SubprocessResult(
            args=list(args),
            cwd=str(cwd),
            returncode=0,
            stdout="good.py:12:ok preview\nbad.js:notanint:minified blob\n",
            stderr="",
            timed_out=False,
            duration_ms=1,
        )

    monkeypatch.setattr("infra.pipeline.stages.repo_retriever.run_subprocess", fake_run_subprocess)

    hits = _collect_rg_hits(repo_root, "rg", "reactagent", "symbol", 30)
    assert hits == [{"path": "good.py", "line": 12, "preview": "ok preview"}]

    batched_hits, batches = _collect_rg_hits_batched(repo_root, "rg", ["reactagent"], "symbol", 30)
    assert batches == 1
    assert batched_hits == [{"path": "good.py", "line": 12, "preview": "ok preview", "query": "reactagent"}]


def test_scheduler_owner_collapse_prefers_scheduled_jobs_and_paired_test() -> None:
    result_obj = {
        "job_contract": {
            "repo_root": ".",
            "title": "Scheduled Execution Robustness Audit and Hardening Pass",
            "description": "make scheduled execution more robust against duplicate launches, stale claims, and bad startup recovery",
            "objective": "harden scheduled job lifecycle, claims, retries, and restart recovery without drifting into unrelated web routes",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "system_hardening",
            "task_families": ["scheduler_hardening", "scheduler_safety"],
            "secondary_tags": ["scheduler_hardening"],
            "ambiguity_score": 0.18,
        },
        "search_plan": {"subsystem_hypotheses": [], "query_coverage_map": {}},
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "infra/controlplane/scheduled_jobs.py",
                    lexical=4.0,
                    structural=5.0,
                    graph=2.0,
                    matched_terms=["scheduler", "stale claim", "duplicate", "startup recovery"],
                    covers=["recovery_responsibility", "dispatch_path", "state_mutation", "validation_path"],
                ),
                _candidate(
                    "infra/controlplane/scheduler_retry_manager.py",
                    lexical=3.2,
                    structural=4.0,
                    graph=1.3,
                    matched_terms=["scheduler", "retry", "recovery"],
                    covers=["recovery_responsibility", "validation_path"],
                ),
                _candidate(
                    "infra/bin/scheduler_watchdog.py",
                    lexical=3.1,
                    structural=3.3,
                    graph=1.1,
                    matched_terms=["watchdog", "scheduler", "stale claim"],
                    covers=["recovery_responsibility"],
                ),
                _candidate(
                    "infra/controlplane/scheduler_pipeline_healer.py",
                    lexical=3.0,
                    structural=3.0,
                    graph=1.0,
                    matched_terms=["scheduler", "healer", "recovery"],
                    covers=["recovery_path"],
                ),
                _candidate(
                    "tests/control_plane/test_scheduled_jobs.py",
                    lexical=2.0,
                    structural=3.0,
                    graph=1.0,
                    matched_terms=["scheduler", "duplicate", "recovery"],
                    covers=["validation_path"],
                ),
                _candidate(
                    "infra/webui/server_routes_and_main.py",
                    lexical=2.0,
                    structural=3.5,
                    graph=0.8,
                    matched_terms=["scheduler", "operator"],
                    covers=["operator_flow"],
                ),
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())["ranked_context"]
    implementation_targets = [entry["path"] for entry in ranked["implementation_targets"]]

    assert implementation_targets == [
        "infra/controlplane/scheduled_jobs.py",
        "tests/control_plane/test_scheduled_jobs.py",
    ]
    assert "infra/bin/scheduler_watchdog.py" not in implementation_targets
    assert "infra/controlplane/scheduler_pipeline_healer.py" not in implementation_targets
    assert "infra/webui/server_routes_and_main.py" not in implementation_targets


def test_retrieve_and_rank_prefilter_reserves_non_deterministic_budget(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/controlplane").mkdir(parents=True)
    (repo_root / "tools/bin").mkdir(parents=True)
    deterministic_paths = []
    for name in [
        "scheduled_jobs.py",
        "scheduler_retry_manager.py",
        "scheduler_claims.py",
        "scheduler_runner.py",
        "scheduler_state.py",
    ]:
        rel = repo_root / "tools/controlplane" / name
        rel.write_text("scheduler retry claim ownership\n", encoding="utf-8")
        deterministic_paths.append(f"infra/controlplane/{name}")
    expansion_path = repo_root / "infra/bin/scheduler_pipeline_healer.py"
    expansion_path.write_text("def heal_pipeline():\n    return True\n", encoding="utf-8")

    calls = []

    def fake_semantic_retrieve(*, repo_root, query_text, config, allowed_paths, forbidden_paths, candidate_paths=None):
        calls.append(list(candidate_paths or []))
        return ([], {
            "semantic_status": "ok",
            "requested_mode": "retrieve_and_rank",
            "effective_mode": "retrieve_and_rank",
            "requested_provider": "stub",
            "effective_provider": "stub",
            "requested_model": "all-minilm",
            "effective_model": "all-minilm",
            "requested_enabled": True,
            "effective_enabled": True,
            "semantic_returned": 0,
            "effective_strategy": "semantic_retrieve_and_rank",
        })

    monkeypatch.setattr("infra.pipeline.stages.repo_retriever.semantic_retrieve", fake_semantic_retrieve)

    cfg = _base_config()
    cfg["semantic"] = {
        **cfg["semantic"],
        "semantic_mode": "retrieve_and_rank",
        "semantic_provider": "stub",
        "enable_semantic_retrieval": True,
        "semantic_prefilter_limit": 4,
        "semantic_prefilter_anchor_limit": 2,
    }

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "allowed_paths": [],
            "forbidden_paths": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "system_hardening",
            "task_families": ["scheduler_hardening"],
            "secondary_tags": ["scheduler_hardening"],
        },
        "search_plan": {
            "queries": [
                {"query": "scheduler", "query_type": "literal", "weight": 1.0},
            ],
            "subsystem_hypotheses": ["tools/controlplane"],
            "search_mode": "targeted_ranked_context",
            "semantic_query_text": "scheduler recovery ownership healer",
        },
        "extracted_terms": {"file_hints": []},
    }

    build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=cfg)

    assert calls, "semantic_retrieve should be invoked"
    candidate_paths = calls[0]
    assert "infra/bin/scheduler_pipeline_healer.py" in candidate_paths
    assert any(path not in set(deterministic_paths) for path in candidate_paths)


def test_semantic_only_code_candidate_keeps_provenance_and_beats_generated_docs(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/webui").mkdir(parents=True)
    (repo_root / "governance/ledger/_items/PIS-TEST").mkdir(parents=True)
    (repo_root / "infra/webui/server_lab_assistant_state.py").write_text(
        "assistant identity runtime state truth ownership\n", encoding="utf-8"
    )
    (repo_root / "infra/webui/server_lab_assistant_execution_truth_ownership.py").write_text(
        "execution truth ownership reconciliation writeback\n", encoding="utf-8"
    )
    (repo_root / "governance/ledger/_items/PIS-TEST/pis.md").write_text(
        "identity truth contract and ledger semantics\n", encoding="utf-8"
    )

    def fake_semantic_retrieve(*, repo_root, query_text, config, allowed_paths, forbidden_paths, candidate_paths=None):
        return ([
            {
                "path": "governance/ledger/_items/PIS-TEST/pis.md",
                "semantic_score": 0.74,
                "chunk_preview": "identity truth contract and ledger semantics",
            },
            {
                "path": "infra/webui/server_lab_assistant_execution_truth_ownership.py",
                "semantic_score": 0.62,
                "chunk_preview": "execution truth ownership reconciliation writeback",
            },
        ], {
            "semantic_status": "ok",
            "requested_mode": "retrieve_and_rank",
            "effective_mode": "retrieve_and_rank",
            "requested_provider": "stub",
            "effective_provider": "stub",
            "requested_model": "all-minilm",
            "effective_model": "all-minilm",
            "requested_enabled": True,
            "effective_enabled": True,
            "semantic_returned": 2,
            "effective_strategy": "semantic_retrieve_and_rank",
        })

    monkeypatch.setattr("infra.pipeline.stages.repo_retriever.semantic_retrieve", fake_semantic_retrieve)

    cfg = _base_config()
    cfg["semantic"] = {
        **cfg["semantic"],
        "semantic_mode": "retrieve_and_rank",
        "semantic_provider": "stub",
        "enable_semantic_retrieval": True,
        "semantic_prefilter_limit": 10,
        "semantic_prefilter_anchor_limit": 3,
        "semantic_candidate_cap": 8,
    }

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "allowed_paths": [],
            "forbidden_paths": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "truth_contract",
            "task_families": ["assistant_truth_and_identity"],
            "secondary_tags": ["assistant_truth_and_identity"],
        },
        "search_plan": {
            "queries": [
                {"query": "assistant", "query_type": "literal", "weight": 1.0},
                {"query": "identity", "query_type": "literal", "weight": 1.0},
            ],
            "search_mode": "targeted_ranked_context",
            "semantic_query_text": "assistant identity truth ownership writeback",
        },
        "extracted_terms": {"file_hints": []},
    }

    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=cfg)["retrieval"]
    by_path = {entry["path"]: entry for entry in retrieval["file_candidates"]}

    code_candidate = by_path["infra/webui/server_lab_assistant_execution_truth_ownership.py"]
    doc_candidate = by_path["governance/ledger/_items/PIS-TEST/pis.md"]

    assert code_candidate["match_origin"] == "semantic_only"
    assert "deterministic" not in code_candidate["evidence_sources"]
    assert retrieval["retrieval_stats"]["semantic_only_candidate_count"] >= 1
    assert code_candidate["scores"]["semantic_score"] > doc_candidate["scores"]["semantic_score"]


def test_retrieval_ranking_prefers_pipeline_planning_and_demotes_runtime_registry(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "infra/pipeline/stages").mkdir(parents=True)
    (repo_root / "infra/controlplane").mkdir(parents=True)
    (repo_root / "infra/webui").mkdir(parents=True)
    (repo_root / "tests/pipeline").mkdir(parents=True)
    (repo_root / "infra/pipeline/planning.py").write_text("def rank_evidence():\n    return 1\n", encoding="utf-8")
    (repo_root / "infra/pipeline/stages/search_plan.py").write_text("def plan_queries():\n    return []\n", encoding="utf-8")
    (repo_root / "infra/pipeline/stages/repo_retriever.py").write_text("def retrieve_candidates():\n    return []\n", encoding="utf-8")
    (repo_root / "infra/controlplane/job_registry.py").write_text("def registry_status():\n    return {}\n", encoding="utf-8")
    (repo_root / "infra/webui/server_lab_assistant_execution_reconciliation.py").write_text("def reconcile():\n    return None\n", encoding="utf-8")
    (repo_root / "tests/pipeline/test_planning_flow.py").write_text("def test_rank_evidence():\n    assert True\n", encoding="utf-8")
    (repo_root / "tests/pipeline/test_stage3_search_plan_engine.py").write_text("def test_search_plan_freshness():\n    assert True\n", encoding="utf-8")

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "title": "Cross-Project Retrieval Ranking, Freshness, and Evidence-First Answer Assembly Contract",
            "description": "define deterministic ranking across direct evidence and memory with freshness and missing evidence warnings",
            "objective": "rank direct evidence above stale memory and preserve applicability boundaries in answer assembly",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "retrieval_ranking_contract",
            "task_families": ["retrieval_ranking_contract"],
            "secondary_tags": [],
            "ambiguity_score": 0.32,
        },
        "search_plan": {"subsystem_hypotheses": ["retrieval_ranker", "validation"], "query_coverage_map": {}},
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "infra/pipeline/planning.py",
                    lexical=5.0,
                    structural=5.0,
                    graph=2.0,
                    matched_terms=["retrieval", "ranking", "freshness", "evidence"],
                    covers=["validation_path", "truth_boundary"],
                ),
                _candidate(
                    "infra/pipeline/stages/search_plan.py",
                    lexical=4.5,
                    structural=4.8,
                    graph=1.8,
                    matched_terms=["retrieval", "ranking", "freshness"],
                    covers=["validation_path"],
                ),
                _candidate(
                    "infra/pipeline/stages/repo_retriever.py",
                    lexical=4.0,
                    structural=4.4,
                    graph=1.6,
                    matched_terms=["retrieval", "evidence"],
                    covers=["validation_path"],
                ),
                _candidate(
                    "infra/controlplane/job_registry.py",
                    lexical=3.7,
                    structural=4.0,
                    graph=1.7,
                    matched_terms=["memory", "evidence"],
                    covers=["task_register_flow"],
                ),
                _candidate(
                    "tests/pipeline/test_planning_flow.py",
                    lexical=2.8,
                    structural=3.2,
                    graph=1.0,
                    matched_terms=["ranking", "freshness"],
                    covers=["validation_path"],
                ),
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())
    implementation_targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]]
    assert implementation_targets == [
        "infra/pipeline/planning.py",
        "tests/pipeline/test_planning_flow.py",
    ]
    assert "infra/controlplane/job_registry.py" not in implementation_targets

    bundled = bundle_context({**result_obj, **ranked}, _base_config())
    patch_plan, warnings = maybe_model_patch_plan({**result_obj, **ranked, **bundled}, _base_config())

    assert warnings
    assert patch_plan["plan_status"] == "VALIDATION_ONLY"
    assert patch_plan["plan_outcome"] == "VALIDATION_ONLY"
    assert [entry["path"] for entry in patch_plan["modify_files"]] == ["tests/pipeline/test_planning_flow.py"]


def test_retrieval_ranking_boosts_symbol_match_when_symbol_appears_in_filename() -> None:
    result_obj = {
        "job_contract": {
            "repo_root": ".",
            "title": "Delete ReactAgent and refactor existing dependencies",
            "description": "Remove the ReactAgent class and update dependent imports.",
            "objective": "Delete ReactAgent and refactor existing dependencies without drifting into unrelated cleanup.",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "refactor",
            "task_families": ["retrieval_ranking_contract"],
            "secondary_tags": [],
            "ambiguity_score": 0.24,
        },
        "search_plan": {"subsystem_hypotheses": ["retrieval_ranker"], "query_coverage_map": {}},
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "agent/core/react_agent/react_agent.py",
                    lexical=2.8,
                    structural=2.4,
                    graph=0.6,
                    matched_terms=["reactagent", "delete", "dependencies"],
                    matched_symbols=["ReactAgent"] * 6,
                    covers=["validation_path"],
                ),
                _candidate(
                    "agent/memory/causal/empirical/clustering.py",
                    lexical=3.8,
                    structural=3.0,
                    graph=1.0,
                    matched_terms=["delete", "dependencies", "cleanup"],
                    covers=["validation_path"],
                ),
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())
    implementation_targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]]
    ranked_by_path = {entry["path"]: entry for entry in ranked["ranked_context"]["ranked_files"]}

    assert implementation_targets[0] == "agent/core/react_agent/react_agent.py"
    assert ranked_by_path["agent/core/react_agent/react_agent.py"]["targetability_score"] > ranked_by_path["agent/memory/causal/empirical/clustering.py"]["targetability_score"]


def test_retrieval_ranking_stage5_does_not_crash_on_owner_role_resolution() -> None:
    result_obj = {
        "job_contract": {
            "repo_root": ".",
            "title": "Overnight Scheduled Chain to Morning Result Scenario Contract",
            "description": "rank retrieval and evidence ownership correctly for overnight orchestration proofing",
            "objective": "prefer planning-layer retrieval owners over adjacent runtime surfaces",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "task_profile": {
            "primary_task_type": "retrieval_ranking_contract",
            "task_families": ["retrieval_ranking_contract"],
            "secondary_tags": [],
            "ambiguity_score": 0.2,
        },
        "search_plan": {"subsystem_hypotheses": ["retrieval_ranker"], "query_coverage_map": {}},
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "infra/pipeline/planning.py",
                    lexical=5.0,
                    structural=5.0,
                    graph=2.0,
                    matched_terms=["retrieval", "ranking", "evidence"],
                    covers=["retrieval_owner"],
                ),
                _candidate(
                    "infra/pipeline/stages/context_ranker.py",
                    lexical=4.0,
                    structural=4.0,
                    graph=1.3,
                    matched_terms=["retrieval", "ranking"],
                    covers=["retrieval_scoring"],
                ),
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())

    implementation_targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]]
    assert "infra/pipeline/planning.py" in implementation_targets


def test_non_scheduler_chat_prompt_rescues_chat_owner_from_scheduler_fallback() -> None:
    result_obj = {
        "job_contract": {
            "repo_root": ".",
            "title": "Explicit Chat Persistence State Machine and Recovery Contract",
            "description": "make chat persistence explicit across restart and disconnect without turning scheduler into the primary owner",
            "objective": "promote chat state machine, reducer, and http chat transport ownership for recovery",
            "constraints": [],
            "acceptance_criteria": [],
            "issue_context": {},
        },
        "intake_assessment": {},
        "task_profile": {
            "primary_task_type": "generic",
            "task_families": ["generic"],
            "secondary_tags": [],
            "ambiguity_score": 0.45,
        },
        "search_plan": {"subsystem_hypotheses": ["chat_state"], "query_coverage_map": {}},
        "retrieval": {
            "file_candidates": [
                _candidate(
                    "infra/controlplane/scheduled_jobs.py",
                    lexical=5.2,
                    structural=4.8,
                    graph=2.1,
                    matched_terms=["chat", "recovery"],
                    covers=["dispatch_path", "recovery_responsibility"],
                ),
                _candidate(
                    "tests/control_plane/test_scheduled_jobs.py",
                    lexical=3.1,
                    structural=2.9,
                    graph=1.0,
                    matched_terms=["recovery"],
                    covers=["validation_path"],
                ),
                _candidate(
                    "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatViewModel.kt",
                    lexical=4.9,
                    structural=4.4,
                    graph=1.9,
                    matched_terms=["chat", "persistence", "state", "recovery"],
                    covers=["state_mutation"],
                ),
                _candidate(
                    "infra/mobile/android/app/src/main/java/com/eiel/chat/data/HttpChatApi.kt",
                    lexical=4.2,
                    structural=4.0,
                    graph=1.7,
                    matched_terms=["chat", "transport", "disconnect"],
                    covers=["validation_path"],
                ),
            ]
        },
    }

    ranked = rank_context(result_obj, _base_config())
    implementation_targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]]

    assert "infra/controlplane/scheduled_jobs.py" not in implementation_targets
    assert implementation_targets[0] == "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatViewModel.kt"
