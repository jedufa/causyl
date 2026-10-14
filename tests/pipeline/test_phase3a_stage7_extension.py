from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from infra.pipeline.core import build_envelope, load_json, utc_now_iso
from infra.pipeline.planning import _fallback_promotable_target, build_execution_contract, bundle_context, rank_context
from infra.pipeline.schemas import validate_execution_contract_payload, validate_patch_plan_result
from infra.pipeline.stages import patch_plan as patch_plan_stage


REAL_REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = REAL_REPO_ROOT / "infra/pipeline/knowledge"


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
            "execution_contract_total_chars": 18000,
            "execution_contract_full_file_chars": 12000,
            "execution_contract_max_full_files": 3,
        },
        "semantic": {
            "semantic_score_weight": 0.35,
            "semantic_mode": "off",
            "semantic_provider": "none",
            "enable_semantic_retrieval": False,
        },
        "planning": {"enable_model": False},
        "knowledge_assets": {"base_dir": str(KNOWLEDGE_DIR)},
        "pipeline": {"tool_version": "test"},
        "_stage": {"allow_remote_model": False},
    }


DEFAULT_INTAKE = {
    "failure_taxonomy": {
        "operator_failure_modes": ["operator-facing truth drift"],
        "system_failure_modes": ["ranking evidence mismatch"],
        "concurrency_failure_modes": ["duplicate or contested ownership"],
        "recovery_failure_modes": ["stale recovery path"],
    },
    "invariants": [
        "Ranking must prefer direct evidence over stale summaries",
        "Operator-visible state must remain truthful and inspectable",
    ],
    "derived_requirements": {
        "safety_requirements": ["reject passive summary-only surfaces as owners"],
        "observability_requirements": ["preserve evidence ordering and provenance"],
        "recovery_guarantees": ["recovery state must remain bounded and explicit"],
    },
}


DEFAULT_SEARCH_PLAN = {
    "queries": [{"query": "ranking evidence truth", "query_type": "literal", "weight": 1.0}],
    "search_mode": "targeted_ranked_context",
    "subsystem_hypotheses": ["scoring_engine"],
    "query_coverage_map": {
        "ranking evidence truth": {
            "subsystems": ["scoring_engine"],
            "covers": ["ranking_evidence", "operator_flow"],
            "cross_cutting_flags": ["operator_safety", "validation_rigor"],
        }
    },
}


DEFAULT_TASK_PROFILE = {
    "primary_task_type": "bugfix",
    "secondary_tags": ["retrieval_ranking_contract"],
    "task_families": ["retrieval_ranking_contract"],
    "cross_cutting_flags": ["operator_safety", "validation_rigor"],
    "task_archetype": "implementation_change",
    "output_contract_mode": "patch_contract",
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
    preview: str | None = None,
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
                "end_line": 3,
                "preview": preview or " ".join(list(matched_terms) + symbols)[:240],
            }
        ],
        "reason_codes": ["keyword_match"],
        "semantic_evidence": [],
        "evidence_sources": list(evidence_sources or ["deterministic"]),
        "covers": list(covers),
        "coverage_score": float(len(covers)),
        "superficial_match_penalty": 0.0,
    }


def _rank_input(
    file_candidates: list[dict],
    *,
    repo_root: Path = REAL_REPO_ROOT,
    job_overrides: dict | None = None,
    task_profile: dict | None = None,
    search_plan: dict | None = None,
    intake_assessment: dict | None = None,
) -> dict:
    job_contract = {
        "job_id": "JOB-TEST",
        "repo_root": str(repo_root),
        "title": "Tighten ranking truth and evidence ordering",
        "description": "Ensure ranking prefers direct evidence over summaries and demote passive surfaces.",
        "objective": "Tighten ranking truth and evidence ordering.",
        "constraints": ["keep changes inside pipeline planning code"],
        "acceptance_criteria": ["true owner outranks hubs", "bundle surfaces ranking validation"],
        "issue_context": {},
    }
    if job_overrides:
        job_contract.update(job_overrides)
    profile = deepcopy(DEFAULT_TASK_PROFILE)
    if task_profile:
        profile.update(task_profile)
    plan = deepcopy(DEFAULT_SEARCH_PLAN)
    if search_plan:
        plan.update(search_plan)
    return {
        "job_contract": job_contract,
        "intake_assessment": deepcopy(intake_assessment or DEFAULT_INTAKE),
        "task_profile": profile,
        "search_plan": plan,
        "retrieval": {"file_candidates": list(file_candidates)},
    }


def _snippet_text(path: str, *, start_line: int = 1, end_line: int = 20) -> str:
    abs_path = REAL_REPO_ROOT / path
    return "\n".join(abs_path.read_text(encoding="utf-8", errors="replace").splitlines()[start_line - 1 : end_line])


def _bundle_file(
    path: str,
    *,
    role: str,
    bundle_role: str,
    file_role: str,
    edit_allowed: bool,
    snippet_text: str,
    candidate_role: str | None = None,
    ownership_role: str = "core_owner",
    selection_lane: str | None = None,
    selection_reasons: list[str] | None = None,
) -> dict:
    lane = selection_lane or ("implementation_target" if edit_allowed else "reference_context")
    return {
        "path": path,
        "role": role,
        "bundle_role": bundle_role,
        "file_role": file_role,
        "candidate_role": candidate_role or ("implementation_code" if file_role == "implementation_code" else "test_file" if file_role == "test_code" else "other"),
        "ownership_role": ownership_role,
        "selection_lane": lane,
        "edit_allowed": edit_allowed,
        "selection_score": 8.5,
        "targetability_score": 8.5 if edit_allowed else 2.4,
        "reference_value_score": 2.0 if edit_allowed else 6.2,
        "semantic_rescued": False,
        "selection_reasons": selection_reasons or ["selected for execution contract test"],
        "responsibility": ["ranking_evidence"],
        "covers_failure_modes": ["system_failure_modes"],
        "subsystems": ["scoring_engine"],
        "coverage_detail": {
            "score": 1.2,
            "matched_failure_categories": ["system_failure_modes"],
            "matched_invariant_buckets": ["ranking_evidence"],
            "matched_cross_cutting_flags": ["operator_safety"],
            "matched_subsystems": ["scoring_engine"],
            "matched_query_coverage": ["ranking_evidence"],
            "coverage_breadth": 4,
        },
        "passive_relevance": {"score": 0.0, "categories": [], "reasons": []},
        "ownership_confidence": {"score": 0.9, "notes": ["core owner"]},
        "header_context": None,
        "snippets": [
            {
                "snippet_id": f"{path}::1-20::1",
                "kind": "line_window",
                "snippet_role": "primary_edit" if edit_allowed else "reference_context",
                "symbol": None,
                "start_line": 1,
                "end_line": 20,
                "allowed_edit_region": {"start_line": 1, "end_line": 20} if edit_allowed else None,
                "edit_priority": "high" if edit_allowed else "low",
                "content": snippet_text,
                "selected_because": "test snippet",
            }
        ],
    }


def test_coverage_scoring_rewards_files_that_cover_more_active_concerns() -> None:
    result = _rank_input(
        [
            _candidate(
                "infra/pipeline/planning.py",
                lexical=2.3,
                structural=1.6,
                graph=1.3,
                matched_terms=["ranking", "evidence", "truth", "operator"],
                matched_symbols=["rank_context", "bundle_context"],
                covers=["ranking_evidence", "validation_path", "operator_flow", "state_mutation"],
            ),
            _candidate(
                "infra/pipeline/run_summary.py",
                lexical=2.4,
                structural=0.7,
                graph=0.2,
                matched_terms=["ranking", "evidence"],
                covers=["validation_path"],
            ),
        ]
    )

    config = _base_config()
    config["limits"]["min_file_score"] = 0.1
    ranked = rank_context(result, config)["ranked_context"]["ranked_files"]
    by_path = {entry["path"]: entry for entry in ranked}

    assert by_path["infra/pipeline/planning.py"]["coverage_detail"]["score"] > by_path["infra/pipeline/run_summary.py"]["coverage_detail"]["score"]
    assert by_path["infra/pipeline/planning.py"]["targetability_score"] > by_path["infra/pipeline/run_summary.py"]["targetability_score"]


def test_superficial_match_penalty_demotes_broad_hub_behind_true_owner() -> None:
    result = _rank_input(
        [
            _candidate(
                "infra/pipeline/planning.py",
                lexical=2.5,
                structural=1.7,
                graph=1.1,
                matched_terms=["ranking", "evidence", "freshness", "owner"],
                matched_symbols=["rank_context"],
                covers=["ranking_evidence", "validation_path", "operator_flow", "state_mutation"],
            ),
            _candidate(
                "infra/pipeline/search_plan.py",
                lexical=2.9,
                structural=0.5,
                graph=0.1,
                matched_terms=["ranking", "evidence", "freshness"],
                covers=[],
                preview="ranking evidence freshness planning plan pipeline stage",
            ),
        ]
    )

    config = _base_config()
    config["limits"]["min_file_score"] = 0.1
    ranked = rank_context(result, config)["ranked_context"]["ranked_files"]
    by_path = {entry["path"]: entry for entry in ranked}

    assert by_path["infra/pipeline/search_plan.py"]["superficial_match_penalty"] > 0
    assert by_path["infra/pipeline/planning.py"]["targetability_score"] > by_path["infra/pipeline/search_plan.py"]["targetability_score"]


def test_passive_relevance_penalty_demotes_route_and_summary_surfaces_when_not_owner() -> None:
    result = _rank_input(
        [
            _candidate(
                "infra/pipeline/planning.py",
                lexical=2.2,
                structural=1.5,
                graph=1.2,
                matched_terms=["operator", "truth", "ranking"],
                matched_symbols=["rank_context"],
                covers=["ranking_evidence", "validation_path", "operator_flow", "state_mutation"],
            ),
            _candidate(
                "server_routes_and_main.py",
                lexical=2.8,
                structural=0.3,
                graph=0.1,
                matched_terms=["operator", "truth", "ranking", "route"],
                covers=["operator_flow"],
                preview="route router projection summary operator ranking truth",
            ),
        ]
    )

    config = _base_config()
    config["limits"]["min_file_score"] = 0.1
    ranked = rank_context(result, config)["ranked_context"]["ranked_files"]
    by_path = {entry["path"]: entry for entry in ranked}

    assert by_path["server_routes_and_main.py"]["passive_relevance_penalty"] > 0
    assert by_path["server_routes_and_main.py"]["selection_lane"] == "reference_context"
    assert by_path["infra/pipeline/planning.py"]["targetability_score"] > by_path["server_routes_and_main.py"]["targetability_score"]


def test_bundle_gap_warning_emits_when_ranking_bundle_lacks_test_surface() -> None:
    result = _rank_input(
        [
            _candidate(
                "infra/pipeline/planning.py",
                lexical=2.4,
                structural=1.8,
                graph=1.2,
                matched_terms=["ranking", "evidence", "truth"],
                matched_symbols=["rank_context"],
                covers=["ranking_evidence", "validation_path", "operator_flow", "state_mutation"],
            )
        ],
        job_overrides={"issue_context": {"changed_files_hint": ["infra/pipeline/planning.py"]}},
    )
    ranked = rank_context(result, _base_config())
    bundled = bundle_context(ranked, _base_config())["context_bundle"]

    assert bundled["bundle_gap_warning"] is True
    assert "missing_ranking_tests" in bundled["coverage_validation"]["warning_markers"]
    assert any("Ranking or evidence task lacks paired pipeline validation coverage" in item for item in bundled["bundle_gap_details"])


def test_bundle_context_promotes_explicit_test_request_into_editable_targets_for_generic_tasks() -> None:
    result = _rank_input(
        [
            _candidate(
                "infra/pipeline/planning.py",
                lexical=2.6,
                structural=1.8,
                graph=1.2,
                matched_terms=["normalize", "planning", "tests"],
                matched_symbols=["build_execution_contract"],
                covers=["state_mutation", "validation_path"],
            ),
            _candidate(
                "tests/pipeline/test_planning_flow.py",
                lexical=2.2,
                structural=1.2,
                graph=0.9,
                matched_terms=["tests", "planning", "validation"],
                covers=["validation_path"],
            ),
        ],
        job_overrides={
            "title": "Update planning behavior and add tests",
            "description": "Update infra/pipeline/planning.py and add tests in tests/pipeline/test_planning_flow.py.",
            "objective": "Update infra/pipeline/planning.py and add tests in tests/pipeline/test_planning_flow.py.",
        },
        task_profile={
            "primary_task_type": "feature",
            "secondary_tags": ["generic"],
            "task_families": ["generic"],
            "cross_cutting_flags": [],
            "task_archetype": "implementation_change",
            "output_contract_mode": "patch_contract",
        },
    )
    ranked = rank_context(result, _base_config())
    bundled = bundle_context(ranked, _base_config())["context_bundle"]

    editable_paths = [str(entry.get("path") or "") for entry in bundled["editable_targets"]]
    assert "infra/pipeline/planning.py" in editable_paths
    assert "tests/pipeline/test_planning_flow.py" in editable_paths
    assert bundled["bundle_metrics"]["editable_target_count"] >= 2


def test_bundle_context_marks_app_test_validation_surface_as_editable_when_prompt_requires_tests(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tests").mkdir(parents=True)
    (repo_root / "app.py").write_text("def normalize_text(s):\n    return s.strip()\n", encoding="utf-8")
    (repo_root / "tests/test_app.py").write_text(
        "from app import normalize_text\n\n"
        "def test_empty_input_returns_empty_string():\n"
        "    assert normalize_text(\"\") == \"\"\n",
        encoding="utf-8",
    )

    result = _rank_input(
        [
            _candidate(
                "app.py",
                lexical=2.6,
                structural=1.8,
                graph=1.2,
                matched_terms=["normalize", "text", "trim"],
                matched_symbols=["normalize_text"],
                covers=["state_mutation", "validation_path"],
            ),
            _candidate(
                "tests/test_app.py",
                lexical=2.2,
                structural=1.2,
                graph=0.9,
                matched_terms=["tests", "normalize", "validation"],
                covers=["validation_path"],
            ),
        ],
        repo_root=repo_root,
        job_overrides={
            "title": "Refactor normalize_text and update tests",
            "description": "Update app.py and update tests/test_app.py with exact regression coverage.",
            "objective": "Refactor normalize_text in app.py and update tests/test_app.py by adding exact regression tests.",
        },
        task_profile={
            "primary_task_type": "feature",
            "secondary_tags": ["admission_and_validation"],
            "task_families": ["admission_and_validation"],
            "cross_cutting_flags": ["validation_rigor"],
            "task_archetype": "implementation_change",
            "output_contract_mode": "patch_contract",
        },
    )
    ranked = rank_context(result, _base_config())
    bundled = bundle_context(ranked, _base_config())["context_bundle"]

    editable_paths = [str(entry.get("path") or "") for entry in bundled["editable_targets"]]
    paired_test = next(entry for entry in bundled["editable_targets"] if str(entry.get("path") or "") == "tests/test_app.py")

    assert "app.py" in editable_paths
    assert "tests/test_app.py" in editable_paths
    assert paired_test["bundle_role"] == "paired_test"
    assert paired_test["edit_allowed"] is True
    assert bundled["bundle_metrics"]["editable_target_count"] >= 2


def test_no_regression_for_existing_owner_collapse_families() -> None:
    cases = [
        (
            {
                "primary_task_type": "bugfix",
                "secondary_tags": ["orchestration_checkpointing"],
                "task_families": ["orchestration_checkpointing"],
                "cross_cutting_flags": ["recovery_integrity", "duplicate_suppression"],
                "task_archetype": "implementation_change",
                "output_contract_mode": "patch_contract",
            },
            [
                _candidate("server_agents_orchestrator.py", lexical=2.0, structural=1.9, graph=1.2, matched_terms=["checkpoint", "resume"], covers=["state_mutation", "validation_path", "ownership_control"]),
                _candidate("server_routes_and_main.py", lexical=2.1, structural=0.5, graph=0.2, matched_terms=["checkpoint", "resume"], covers=["operator_flow"]),
                _candidate("test_webui_agents_orchestration.py", lexical=1.7, structural=1.1, graph=0.9, matched_terms=["checkpoint"], covers=["validation_path"]),
            ],
            "server_agents_orchestrator.py",
        ),
        (
            {
                "primary_task_type": "bugfix",
                "secondary_tags": ["scheduler_hardening"],
                "task_families": ["scheduler_safety"],
                "cross_cutting_flags": ["recovery_integrity", "state_consistency"],
                "task_archetype": "implementation_change",
                "output_contract_mode": "patch_contract",
            },
            [
                _candidate("scheduled_jobs.py", lexical=2.1, structural=1.7, graph=1.0, matched_terms=["scheduler", "recovery"], covers=["recovery_responsibility", "dispatch_path", "state_mutation"]),
                _candidate("server_maintenance.py", lexical=2.0, structural=0.5, graph=0.2, matched_terms=["scheduler", "recovery"], covers=["operator_flow"]),
                _candidate("test_scheduled_jobs.py", lexical=1.6, structural=1.0, graph=0.8, matched_terms=["scheduler"], covers=["validation_path"]),
            ],
            "scheduled_jobs.py",
        ),
        (
            {
                "primary_task_type": "bugfix",
                "secondary_tags": ["ui_truth_projection"],
                "task_families": ["ui_truth_projection"],
                "cross_cutting_flags": ["operator_safety", "stale_state_handling"],
                "task_archetype": "implementation_change",
                "output_contract_mode": "patch_contract",
            },
            [
                _candidate("server_lab_assistant_operator_snapshot.py", lexical=2.2, structural=1.8, graph=1.0, matched_terms=["snapshot", "truth"], covers=["validation_path", "state_mutation", "operator_flow"]),
                _candidate("server_routes_and_main.py", lexical=2.3, structural=0.4, graph=0.2, matched_terms=["snapshot", "truth", "route"], covers=["operator_flow"]),
                _candidate("test_lab_assistant_operator_snapshot.py", lexical=1.7, structural=1.0, graph=0.8, matched_terms=["snapshot"], covers=["validation_path"]),
            ],
            "server_lab_assistant_operator_snapshot.py",
        ),
    ]

    for task_profile, candidates, expected_owner in cases:
        result = _rank_input(candidates, task_profile=task_profile)
        ranked = rank_context(result, _base_config())["ranked_context"]
        assert ranked["implementation_targets"][0]["path"] == expected_owner


def test_execution_contract_hydrates_edit_targets_and_keeps_support_context_bounded() -> None:
    bundle_obj = {
        "job_id": "JOB-EXEC-1",
        "run_id": "RUN-EXEC-1",
        "job_contract": {
            "job_id": "JOB-EXEC-1",
            "repo_root": str(REAL_REPO_ROOT),
            "objective": "Tighten patch planner execution handoff.",
            "constraints": ["only modify patch planner stage"],
            "acceptance_criteria": ["edit target is fully hydrated", "support files remain bounded"],
        },
        "task_profile": {
            "task_archetype": "implementation_change",
            "output_contract_mode": "patch_contract",
        },
        "context_bundle": {
            "files": [
                _bundle_file(
                    "infra/pipeline/stages/patch_plan.py",
                    role="primary_target",
                    bundle_role="primary_target",
                    file_role="implementation_code",
                    edit_allowed=True,
                    snippet_text=_snippet_text("infra/pipeline/stages/patch_plan.py", end_line=20),
                ),
                _bundle_file(
                    "tests/pipeline/test_planning_flow.py",
                    role="paired_test",
                    bundle_role="paired_test",
                    file_role="test_code",
                    edit_allowed=False,
                    snippet_text=_snippet_text("tests/pipeline/test_planning_flow.py", end_line=20),
                    candidate_role="test_file",
                    ownership_role="test_surface",
                ),
            ],
            "editable_targets": [],
            "read_only_context": [],
            "bundle_confidence": 0.82,
            "bundle_ready_for_execution": True,
            "bundle_weakness_reasons": [],
        },
    }
    patch_plan = {
        "objective": "Tighten patch planner execution handoff.",
        "modify_files": [
            {
                "path": "infra/pipeline/stages/patch_plan.py",
                "intended_modifications": ["write additive execution contract artifact"],
                "rationale": ["Stage 8 should not reconstruct intent"],
            }
        ],
        "files_read_only_context": ["tests/pipeline/test_planning_flow.py"],
        "required_tests": ["pytest -q tests/pipeline/test_phase3a_stage7_extension.py"],
        "assumptions": ["stage 7 output remains additive"],
        "unresolved_uncertainties": [],
        "risks": ["budget regression"],
        "output_contract_mode": "patch_contract",
        "must_not_change": ["tests/pipeline/test_planning_flow.py"],
    }

    contract = build_execution_contract(bundle_obj, patch_plan, _base_config())
    validate_execution_contract_payload(contract)
    execution_contract = contract["execution_contract"]
    generation_files = {entry["path"]: entry for entry in execution_contract["generation_files"]}

    assert generation_files["infra/pipeline/stages/patch_plan.py"]["include_mode"] == "full_file"
    assert "content" in generation_files["infra/pipeline/stages/patch_plan.py"]
    assert generation_files["tests/pipeline/test_planning_flow.py"]["include_mode"] in {"snippets_only", "reference_only"}


def test_execution_contract_never_self_forbids_an_approved_edit_target() -> None:
    bundle_obj = {
        "job_id": "JOB-EXEC-SELF-FORBID",
        "run_id": "RUN-EXEC-SELF-FORBID",
        "job_contract": {
            "job_id": "JOB-EXEC-SELF-FORBID",
            "repo_root": str(REAL_REPO_ROOT),
            "objective": "Tighten infra/bin/jeff help output.",
            "constraints": ["only modify infra/bin/jeff"],
            "acceptance_criteria": ["infra/bin/jeff remains editable"],
        },
        "task_profile": {
            "task_archetype": "implementation_change",
            "output_contract_mode": "patch_contract",
        },
        "context_bundle": {
            "files": [
                _bundle_file(
                    "infra/bin/jeff",
                    role="primary_target",
                    bundle_role="primary_target",
                    file_role="implementation_code",
                    edit_allowed=True,
                    snippet_text=_snippet_text("infra/bin/jeff", end_line=20),
                ),
            ],
            "editable_targets": [
                _bundle_file(
                    "infra/bin/jeff",
                    role="primary_target",
                    bundle_role="primary_target",
                    file_role="implementation_code",
                    edit_allowed=True,
                    snippet_text=_snippet_text("infra/bin/jeff", end_line=20),
                ),
            ],
            "read_only_context": [
                _bundle_file(
                    "infra/bin/jeff",
                    role="reference_context",
                    bundle_role="reference_context",
                    file_role="implementation_code",
                    edit_allowed=False,
                    snippet_text=_snippet_text("infra/bin/jeff", end_line=20),
                ),
            ],
            "bundle_confidence": 0.82,
            "bundle_ready_for_execution": True,
            "bundle_weakness_reasons": [],
        },
    }
    patch_plan = {
        "objective": "Tighten infra/bin/jeff help output.",
        "modify_files": [
            {
                "path": "infra/bin/jeff",
                "intended_modifications": ["clarify default wrapper behavior"],
                "rationale": ["explicit single-file target"],
            }
        ],
        "files_read_only_context": ["infra/bin/jeff"],
        "required_tests": [],
        "assumptions": [],
        "unresolved_uncertainties": [],
        "risks": [],
        "output_contract_mode": "patch_contract",
        "must_not_change": ["infra/bin/jeff"],
        "forbidden_targets": ["infra/bin/jeff", "*.md"],
    }

    contract = build_execution_contract(bundle_obj, patch_plan, _base_config())
    validate_execution_contract_payload(contract)
    execution_contract = contract["execution_contract"]

    assert execution_contract["allowed_edit_files"] == ["infra/bin/jeff"]
    assert "infra/bin/jeff" not in execution_contract["forbidden_edit_files"]
    assert "infra/bin/jeff" not in execution_contract["constraints"]["must_not_change"]


def test_execution_contract_design_mode_allows_expected_new_files_without_forcing_modify_files() -> None:
    bundle_obj = {
        "job_id": "JOB-EXEC-2",
        "run_id": "RUN-EXEC-2",
        "job_contract": {
            "job_id": "JOB-EXEC-2",
            "repo_root": str(REAL_REPO_ROOT),
            "objective": "Design a new execution adapter surface.",
            "constraints": ["do not mutate unrelated stages"],
            "acceptance_criteria": ["expected new file is named", "integration anchors are preserved"],
        },
        "task_profile": {
            "task_archetype": "design_new_surface",
            "output_contract_mode": "design_execution_contract",
        },
        "context_bundle": {
            "files": [
                _bundle_file(
                    "infra/pipeline/stages/patch_plan.py",
                    role="integration_context",
                    bundle_role="integration_context",
                    file_role="implementation_code",
                    edit_allowed=False,
                    snippet_text=_snippet_text("infra/pipeline/stages/patch_plan.py", end_line=20),
                    ownership_role="boundary_sibling",
                ),
                _bundle_file(
                    "infra/pipeline/planning.py",
                    role="interface_contract_context",
                    bundle_role="interface_contract_context",
                    file_role="implementation_code",
                    edit_allowed=False,
                    snippet_text=_snippet_text("infra/pipeline/planning.py", end_line=20),
                    ownership_role="boundary_sibling",
                ),
            ],
            "editable_targets": [],
            "read_only_context": [],
            "bundle_confidence": 0.61,
            "bundle_ready_for_execution": False,
            "bundle_weakness_reasons": ["design task has no edit targets yet"],
        },
    }
    patch_plan = {
        "objective": "Design a new execution adapter surface.",
        "modify_files": [],
        "files_read_only_context": ["infra/pipeline/stages/patch_plan.py", "infra/pipeline/planning.py"],
        "required_tests": ["pytest -q tests/pipeline"],
        "assumptions": ["new adapter will follow existing stage naming"],
        "unresolved_uncertainties": ["exact integration seam is still open"],
        "risks": ["new file may require follow-on implementation task"],
        "output_contract_mode": "design_execution_contract",
        "design_execution_contract": {
            "new_artifacts_expected": ["infra/pipeline/stages/execution_adapter.py"],
            "supporting_reference_files": ["infra/pipeline/planning.py"],
            "integration_points": ["infra/pipeline/stages/patch_plan.py"],
            "constraints": ["keep stage 7 additive"],
            "validation_expectations": ["schema validation"],
            "open_design_questions": ["should adapter own hydration or only packaging?"],
        },
    }

    contract = build_execution_contract(bundle_obj, patch_plan, _base_config())
    validate_execution_contract_payload(contract)
    execution_contract = contract["execution_contract"]

    assert execution_contract["allowed_edit_files"] == []
    assert execution_contract["expected_new_files"] == ["infra/pipeline/stages/execution_adapter.py"]
    assert execution_contract["execution_mode"] == "design_contract_only"
    included_paths = {entry["path"] for entry in execution_contract["included_files"]}
    assert "infra/pipeline/stages/patch_plan.py" in included_paths
    assert "infra/pipeline/planning.py" in included_paths


def test_execution_contract_marks_repo_assessment_as_not_patch_ready() -> None:
    bundle_obj = {
        "job_id": "JOB-EXEC-3",
        "run_id": "RUN-EXEC-3",
        "job_contract": {
            "job_id": "JOB-EXEC-3",
            "repo_root": str(REAL_REPO_ROOT),
            "objective": "Assess pipeline planning quality.",
            "constraints": [],
            "acceptance_criteria": ["report findings, do not mutate code"],
        },
        "task_profile": {
            "task_archetype": "repo_assessment",
            "output_contract_mode": "assessment_report_contract",
        },
        "context_bundle": {
            "files": [
                _bundle_file(
                    "infra/pipeline/planning.py",
                    role="reference_context",
                    bundle_role="reference_context",
                    file_role="implementation_code",
                    edit_allowed=False,
                    snippet_text=_snippet_text("infra/pipeline/planning.py", end_line=20),
                    ownership_role="boundary_sibling",
                )
            ],
            "editable_targets": [],
            "read_only_context": [],
            "bundle_confidence": 0.57,
            "bundle_ready_for_execution": False,
            "bundle_weakness_reasons": [],
        },
    }
    patch_plan = {
        "objective": "Assess pipeline planning quality.",
        "modify_files": [],
        "files_read_only_context": ["infra/pipeline/planning.py"],
        "required_tests": [],
        "assumptions": [],
        "unresolved_uncertainties": [],
        "risks": ["assessment may miss dynamic behavior"],
        "output_contract_mode": "assessment_report_contract",
        "assessment_report_contract": {
            "findings": ["ranking and bundling are intertwined"],
            "evidence_files": ["infra/pipeline/planning.py"],
            "risk_areas": ["budget drift"],
            "confidence": 0.57,
            "missing_evidence": [],
            "recommended_next_checks": ["compare with gold suite"],
        },
    }

    contract = build_execution_contract(bundle_obj, patch_plan, _base_config())
    validate_execution_contract_payload(contract)
    execution_contract = contract["execution_contract"]

    assert execution_contract["generation_applicable"] is False
    assert execution_contract["execution_mode"] == "repo_assessment"
    assert "review-only" in execution_contract["generation_blocked_reason"]


def test_fallback_target_promotion_keeps_best_viable_implementation_candidate() -> None:
    fallback = _fallback_promotable_target(
        [
            {
                "path": "server_routes_and_main.py",
                "candidate_role": "implementation_code",
                "ownership_role": "hub",
                "targetability_score": 0.2,
                "reference_value_score": 4.3,
                "passive_relevance_penalty": 1.8,
                "superficial_match_penalty": 2.9,
                "coverage_detail": {},
                "family_owner_fit": 0.0,
                "reasons": ["broad route match"],
            },
            {
                "path": "infra/pipeline/planning.py",
                "candidate_role": "implementation_code",
                "ownership_role": "boundary_sibling",
                "targetability_score": 0.4,
                "reference_value_score": 3.1,
                "passive_relevance_penalty": 0.95,
                "superficial_match_penalty": 0.4,
                "coverage_detail": {
                    "matched_failure_categories": ["system_failure_modes"],
                    "matched_cross_cutting_flags": ["operator_safety"],
                },
                "family_owner_fit": 0.0,
                "reasons": ["near owner with real failure coverage"],
            },
        ],
        families=["retrieval_ranking_contract"],
        task_profile=DEFAULT_TASK_PROFILE,
    )

    assert fallback is not None
    assert fallback["path"] == "infra/pipeline/planning.py"
    assert fallback["selection_lane"] == "implementation_target"
    assert fallback["edit_allowed"] is True
    assert any("fallback implementation target" in reason for reason in fallback["reasons"])


def test_execution_contract_can_recover_edit_scope_from_bundle_when_patch_plan_under_selects() -> None:
    owner_path = "infra/pipeline/stages/patch_plan.py"
    owner_snippet = _snippet_text(owner_path, end_line=20)
    bundle_file = _bundle_file(
        owner_path,
        role="primary_target",
        bundle_role="primary_target",
        file_role="implementation_code",
        edit_allowed=True,
        snippet_text=owner_snippet,
    )
    bundle_obj = {
        "job_id": "JOB-EXEC-FALLBACK",
        "run_id": "RUN-EXEC-FALLBACK",
        "job_contract": {
            "job_id": "JOB-EXEC-FALLBACK",
            "repo_root": str(REAL_REPO_ROOT),
            "objective": "Recover a usable execution scope when planning under-selects.",
            "constraints": ["keep selection deterministic"],
            "acceptance_criteria": ["one edit target remains available"],
        },
        "task_profile": {
            "task_archetype": "implementation_change",
            "output_contract_mode": "patch_contract",
        },
        "context_bundle": {
            "files": [bundle_file],
            "editable_targets": [bundle_file],
            "read_only_context": [],
            "bundle_confidence": 0.79,
            "bundle_ready_for_execution": True,
            "bundle_weakness_reasons": [],
        },
    }
    patch_plan = {
        "objective": "Recover a usable execution scope when planning under-selects.",
        "modify_files": [],
        "files_read_only_context": [],
        "required_tests": [],
        "assumptions": [],
        "unresolved_uncertainties": ["selection floor engaged"],
        "risks": [],
        "output_contract_mode": "patch_contract",
        "plan_status": "INSUFFICIENT_CONTEXT",
    }

    contract = build_execution_contract(bundle_obj, patch_plan, _base_config())
    validate_execution_contract_payload(contract)
    execution_contract = contract["execution_contract"]

    assert execution_contract["fallback_selection_used"] is True
    assert execution_contract["generation_applicable"] is True
    assert execution_contract["allowed_edit_files"] == [owner_path]
    generation_files = {entry["path"]: entry for entry in execution_contract["generation_files"]}
    assert generation_files[owner_path]["include_mode"] == "full_file"


def test_execution_contract_budget_metadata_records_degradation_when_support_context_is_demoted() -> None:
    config = _base_config()
    config["execution_contract_budget"] = {
        "total_chars": 200,
        "full_file_chars": 4000,
        "max_full_files": 1,
        "max_snippets": 2,
    }
    bundle_obj = {
        "job_id": "JOB-EXEC-4",
        "run_id": "RUN-EXEC-4",
        "job_contract": {
            "job_id": "JOB-EXEC-4",
            "repo_root": str(REAL_REPO_ROOT),
            "objective": "Budget test for execution contract packaging.",
            "constraints": [],
            "acceptance_criteria": ["emit degradation metadata when bounded"],
        },
        "task_profile": {
            "task_archetype": "implementation_change",
            "output_contract_mode": "patch_contract",
        },
        "context_bundle": {
            "files": [
                _bundle_file(
                    "infra/pipeline/stages/patch_plan.py",
                    role="primary_target",
                    bundle_role="primary_target",
                    file_role="implementation_code",
                    edit_allowed=True,
                    snippet_text=_snippet_text("infra/pipeline/stages/patch_plan.py", end_line=10),
                ),
                _bundle_file(
                    "tests/pipeline/test_planning_flow.py",
                    role="paired_test",
                    bundle_role="paired_test",
                    file_role="test_code",
                    edit_allowed=False,
                    snippet_text="X" * 800,
                    candidate_role="test_file",
                    ownership_role="test_surface",
                ),
            ],
            "editable_targets": [],
            "read_only_context": [],
            "bundle_confidence": 0.8,
            "bundle_ready_for_execution": True,
            "bundle_weakness_reasons": [],
        },
    }
    patch_plan = {
        "objective": "Budget test for execution contract packaging.",
        "modify_files": [{"path": "infra/pipeline/stages/patch_plan.py", "intended_modifications": ["keep payload bounded"], "rationale": ["budget metadata should be explicit"]}],
        "files_read_only_context": ["tests/pipeline/test_planning_flow.py"],
        "required_tests": ["pytest -q tests/pipeline"],
        "assumptions": [],
        "unresolved_uncertainties": [],
        "risks": [],
        "output_contract_mode": "patch_contract",
    }

    contract = build_execution_contract(bundle_obj, patch_plan, config)
    validate_execution_contract_payload(contract)
    execution_contract = contract["execution_contract"]

    assert execution_contract["prompt_budget"]["degradation_applied"] is True
    assert execution_contract["prompt_budget"]["degradation_actions"]
    support_entry = next(entry for entry in execution_contract["included_files"] if entry["path"] == "tests/pipeline/test_planning_flow.py")
    assert support_entry["include_mode"] == "reference_only"


def test_execution_contract_synthesizes_pytest_command_from_validation_targets() -> None:
    app_bundle = _bundle_file(
        "app.py",
        role="primary_target",
        bundle_role="primary_target",
        file_role="implementation_code",
        edit_allowed=True,
        snippet_text="def normalize_text(s):\n    return s.strip()\n",
    )
    test_bundle = _bundle_file(
        "tests/test_app.py",
        role="paired_test",
        bundle_role="paired_test",
        file_role="test_code",
        edit_allowed=True,
        snippet_text="from app import normalize_text\n",
        candidate_role="test_file",
        ownership_role="test_surface",
        selection_lane="implementation_target",
    )
    bundle_obj = {
        "job_id": "JOB-EXEC-5",
        "run_id": "RUN-EXEC-5",
        "job_contract": {
            "job_id": "JOB-EXEC-5",
            "repo_root": str(REAL_REPO_ROOT),
            "objective": "Update normalize_text and add tests in tests/test_app.py.",
            "constraints": ["modify only app.py and tests/test_app.py"],
            "acceptance_criteria": ["runtime verification executes the focused pytest target"],
        },
        "task_profile": {
            "task_archetype": "implementation_change",
            "output_contract_mode": "patch_contract",
        },
        "context_bundle": {
            "files": [app_bundle, test_bundle],
            "editable_targets": [app_bundle, test_bundle],
            "validation_targets": [test_bundle],
            "read_only_context": [],
            "bundle_confidence": 0.85,
            "bundle_ready_for_execution": True,
            "bundle_weakness_reasons": [],
        },
    }
    patch_plan = {
        "objective": "Update normalize_text and add tests in tests/test_app.py.",
        "modify_files": [
            {"path": "app.py", "intended_modifications": ["update normalization"], "rationale": ["owner file"]},
            {"path": "tests/test_app.py", "intended_modifications": ["add focused regression tests"], "rationale": ["prompt explicitly requests tests"]},
        ],
        "validation_target_files": ["tests/test_app.py"],
        "required_tests": [],
        "assumptions": [],
        "unresolved_uncertainties": [],
        "risks": [],
        "output_contract_mode": "patch_contract",
    }

    contract = build_execution_contract(bundle_obj, patch_plan, _base_config())
    validate_execution_contract_payload(contract)
    execution_contract = contract["execution_contract"]

    assert execution_contract["constraints"]["required_tests"] == ["python3 -m pytest -q tests/test_app.py"]
    assert execution_contract["stage12_runtime_verification"]["required"] is True
    assert execution_contract["stage12_runtime_verification"]["checks"][0]["command"] == "python3 -m pytest -q tests/test_app.py"


def test_stage7_patch_plan_still_writes_legacy_artifact_and_additive_execution_contract(tmp_path: Path) -> None:
    input_path = tmp_path / "06_context_bundle.json"
    output_path = tmp_path / "07_patch_plan.json"
    owner_path = "infra/pipeline/stages/patch_plan.py"
    owner_snippet = _snippet_text(owner_path, end_line=20)
    bundle_file = _bundle_file(
        owner_path,
        role="primary_target",
        bundle_role="primary_target",
        file_role="implementation_code",
        edit_allowed=True,
        snippet_text=owner_snippet,
    )
    result = {
        "job_contract": {
            "job_id": "JOB-STAGE7",
            "repo_root": str(REAL_REPO_ROOT),
            "title": "Additive execution contract output",
            "description": "Emit an execution contract next to the patch plan.",
            "objective": "Emit an execution contract next to the patch plan.",
            "constraints": ["keep stage 7 additive"],
            "acceptance_criteria": ["07_patch_plan.json remains usable", "07_execution_contract.json is emitted"],
            "issue_context": {"changed_files_hint": [owner_path]},
        },
        "task_profile": {
            "primary_task_type": "bugfix",
            "secondary_tags": ["tool_spec_implementation"],
            "task_families": ["tool_spec_implementation"],
            "cross_cutting_flags": ["operator_safety"],
            "task_archetype": "implementation_change",
            "output_contract_mode": "patch_contract",
        },
        "context_bundle": {
            "files": [bundle_file],
            "editable_targets": [bundle_file],
            "read_only_context": [],
            "planning_prompt_material": {
                "issue_summary": "Emit an execution contract next to the patch plan.",
                "constraints": ["keep stage 7 additive"],
                "acceptance_criteria": ["07_patch_plan.json remains usable", "07_execution_contract.json is emitted"],
                "replace_spec": None,
                "selection_mode": "targeted_ranked_context",
                "task_archetype": "implementation_change",
                "output_contract_mode": "patch_contract",
                "editable_target_paths": [owner_path],
                "read_only_context_paths": [],
            },
            "bundle_metrics": {
                "file_count": 1,
                "editable_target_count": 1,
                "read_only_context_count": 0,
                "snippet_count": 1,
                "total_chars": len(owner_snippet),
                "truncated": False,
            },
            "bundle_confidence": 0.83,
            "bundle_ready_for_execution": True,
            "bundle_ready_for_task": True,
            "bundle_role_counts": {"primary_target": 1},
            "file_role_counts": {"implementation_code": 1},
            "bundle_gap_warning": False,
            "bundle_gap_details": [],
            "coverage_validation": {
                "bundle_ready_for_execution": True,
                "weakness_reasons": [],
                "editable_target_count": 1,
                "read_only_context_count": 0,
            },
            "bundle_weakness_reasons": [],
            "truncation_notes": [],
        },
    }
    envelope = build_envelope(
        stage="script_6_context_bundle",
        job_id="JOB-STAGE7",
        run_id="RUN-STAGE7",
        input_payload={"seed": True},
        result=result,
        warnings=[],
        tool_version="test",
        started_at_iso=utc_now_iso(),
        finished_at_iso=utc_now_iso(),
    )
    input_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")

    exit_code = patch_plan_stage.main([
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--repo-root",
        str(REAL_REPO_ROOT),
    ])

    assert exit_code == 0
    patch_plan_payload = load_json(output_path)
    execution_contract_payload = load_json(tmp_path / "07_execution_contract.json")

    validate_patch_plan_result(patch_plan_payload["result"])
    validate_execution_contract_payload(execution_contract_payload)
    assert patch_plan_payload["result"]["patch_plan"]["objective"] == "Emit an execution contract next to the patch plan."
    assert execution_contract_payload["source_patch_plan_artifact"] == "07_patch_plan.json"
