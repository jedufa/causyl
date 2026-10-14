from __future__ import annotations

import argparse
import json
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple


def _normalize(path: str) -> str:
    return str(path or "").strip().replace("\\", "/")


def _extract_stage_summaries(batch_zip: Path) -> Dict[str, Dict[str, Any]]:
    zf = zipfile.ZipFile(batch_zip)
    manifest_name = next(name for name in zf.namelist() if name.endswith("/batch_manifest.json"))
    manifest = json.loads(zf.read(manifest_name))
    name_set = set(zf.namelist())
    out: Dict[str, Dict[str, Any]] = {}
    for entry in list(manifest.get("entries") or []):
        uid = str(entry.get("pis_uid") or "")
        run_id = str((entry.get("run_ids") or [""])[0] or "")
        if not uid or not run_id:
            continue
        try:
            prefix = next(n.rsplit("/runs/", 1)[0] for n in name_set if f"/runs/{run_id}/01_job_contract.json" in n)
        except StopIteration:
            continue
        stage_info: Dict[str, Any] = {}
        for short_name, label in [
            ("02_task_profile.json", "stage2"),
            ("03_search_plan.json", "stage3"),
            ("05_context_ranked.json", "stage5"),
            ("07_patch_plan.json", "stage7"),
        ]:
            stage_path = f"{prefix}/runs/{run_id}/{short_name}"
            if stage_path not in name_set:
                continue
            payload = json.loads(zf.read(stage_path))
            result = dict(payload.get("result") or {})
            if label == "stage2":
                task_profile = dict(result.get("task_profile") or {})
                stage_info[label] = {
                    "primary_task_type": task_profile.get("primary_task_type"),
                    "task_families": list(task_profile.get("task_families") or []),
                }
            elif label == "stage3":
                search_plan = dict(result.get("search_plan") or {})
                stage_info[label] = {
                    "query_count": len(list(search_plan.get("queries") or [])),
                    "target_subsystems": search_plan.get("target_subsystems"),
                }
            elif label == "stage5":
                ranked = dict(result.get("ranked_context") or {})
                stage_info[label] = {
                    "selected_files": [str(item.get("path") or "") for item in list(ranked.get("selected_files") or [])],
                    "implementation_targets": [str(item.get("path") or "") for item in list(ranked.get("implementation_targets") or [])],
                }
            elif label == "stage7":
                patch = dict(result.get("patch_plan") or {})
                stage_info[label] = {
                    "modify_files": [str(item.get("path") or "") for item in list(patch.get("modify_files") or [])],
                }
        if stage_info:
            out[uid] = stage_info
    return out


def _extract_stage_summaries_dir(batch_dir: Path) -> Dict[str, Dict[str, Any]]:
    manifest_path = batch_dir / "batch_manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out: Dict[str, Dict[str, Any]] = {}
    for entry in list(manifest.get("entries") or []):
        uid = str(entry.get("pis_uid") or "")
        report_dir = str(entry.get("report_dir") or "")
        if not uid or not report_dir:
            continue
        report_path = Path(report_dir)
        if not report_path.is_absolute():
            report_path = batch_dir.parent.parent / report_path
        if not report_path.exists():
            continue
        stage_info: Dict[str, Any] = {}
        for short_name, label in [
            ("02_task_profile.json", "stage2"),
            ("03_search_plan.json", "stage3"),
            ("05_context_ranked.json", "stage5"),
            ("07_patch_plan.json", "stage7"),
        ]:
            stage_path = report_path / short_name
            if not stage_path.exists():
                stage_path = report_path / "deterministic" / short_name
            if not stage_path.exists():
                matches = sorted(report_path.glob(f"**/{short_name}"))
                if not matches:
                    continue
                stage_path = matches[0]
            payload = json.loads(stage_path.read_text(encoding="utf-8"))
            result = dict(payload.get("result") or {})
            if label == "stage2":
                task_profile = dict(result.get("task_profile") or {})
                stage_info[label] = {
                    "primary_task_type": task_profile.get("primary_task_type"),
                    "task_families": list(task_profile.get("task_families") or []),
                }
            elif label == "stage3":
                search_plan = dict(result.get("search_plan") or {})
                stage_info[label] = {
                    "query_count": len(list(search_plan.get("queries") or [])),
                    "target_subsystems": search_plan.get("target_subsystems"),
                }
            elif label == "stage5":
                ranked = dict(result.get("ranked_context") or {})
                stage_info[label] = {
                    "selected_files": [str(item.get("path") or "") for item in list(ranked.get("selected_files") or [])],
                    "implementation_targets": [str(item.get("path") or "") for item in list(ranked.get("implementation_targets") or [])],
                }
            elif label == "stage7":
                patch = dict(result.get("patch_plan") or {})
                stage_info[label] = {
                    "modify_files": [str(item.get("path") or "") for item in list(patch.get("modify_files") or [])],
                }
        if stage_info:
            out[uid] = stage_info
    return out


def extract_stage_summaries(batch_source: Path) -> Dict[str, Dict[str, Any]]:
    resolved = batch_source.resolve()
    if resolved.is_dir():
        return _extract_stage_summaries_dir(resolved)
    return _extract_stage_summaries(resolved)


def _path_exists(repo_root: Path, path: str) -> bool:
    norm = _normalize(path)
    if not norm or "*" in norm or "?" in norm or "<" in norm or ">" in norm:
        return False
    return (repo_root / norm).exists()


def _contains_any(paths: Iterable[str], needles: Iterable[str]) -> bool:
    normalized = [_normalize(path) for path in paths]
    return any(any(needle in path for needle in needles) for path in normalized)


def _failure_classes(row: Mapping[str, Any], stage_info: Mapping[str, Any], repo_root: Path) -> List[str]:
    observed = dict(row.get("observed") or {})
    selected = [str(path) for path in list(observed.get("selected_files") or [])]
    reference = dict(row.get("reference") or {})
    reference_targets = [str(path) for path in list(reference.get("implementation_targets") or [])]
    classes: List[str] = []
    if _contains_any(selected, ["infra/controlplane/scheduled_jobs.py", "tests/control_plane/test_scheduled_jobs.py"]):
        classes.append("scheduler_gravity")
    if _contains_any(selected, ["infra/pipeline/planning.py", "infra/pipeline/stages/search_plan.py"]):
        classes.append("pipeline_self_reference")
    if _contains_any(selected, ["knowledge_drafts/benchmark-gpt", "knowledge_drafts/pis_datasets"]):
        classes.append("benchmark_or_dataset_contamination")
    if _contains_any(selected, ["governance/ledger/patch_instruction_sheets/", "docs/"]):
        classes.append("docs_or_governance_contamination")
    if _contains_any(selected, ["infra/mobile/android/"]):
        classes.append("mobile_gravity")
    if not list((row.get("matches") or {}).get("test_hits") or []) and _contains_any(reference_targets, ["tests/", "/src/test/"]):
        classes.append("missing_adjacent_tests")
    existing_ref = [path for path in reference_targets if _path_exists(repo_root, path)]
    if reference_targets and not existing_ref:
        classes.append("gold_reference_needs_review")
    if not classes:
        classes.append("owner_selection_mismatch")
    return classes


def _likely_first_failure_stage(row: Mapping[str, Any], stage_info: Mapping[str, Any], repo_root: Path) -> str:
    classes = set(_failure_classes(row, stage_info, repo_root))
    stage2 = dict(stage_info.get("stage2") or {})
    selected = list(((stage_info.get("stage5") or {}).get("selected_files") or []))
    primary = str(stage2.get("primary_task_type") or "")
    families = [str(item) for item in list(stage2.get("task_families") or [])]
    if "gold_reference_needs_review" in classes:
        return "reference_review_required"
    if "scheduler_gravity" in classes and not any("scheduler" in item for item in [primary] + families):
        return "stage5_context_ranker"
    if "scheduler_gravity" in classes and any("scheduler" in item or "hardening" in item for item in [primary] + families):
        return "stage2_task_classifier"
    if "pipeline_self_reference" in classes or "benchmark_or_dataset_contamination" in classes:
        return "stage5_context_ranker"
    if "docs_or_governance_contamination" in classes:
        return "stage6_context_bundle"
    if selected and not list(((stage_info.get("stage7") or {}).get("modify_files") or [])):
        return "stage7_patch_plan"
    return "stage5_context_ranker"


def _summary_bullets(row: Mapping[str, Any], stage_info: Mapping[str, Any], repo_root: Path) -> List[str]:
    matches = dict(row.get("matches") or {})
    observed = dict(row.get("observed") or {})
    reference = dict(row.get("reference") or {})
    stage2 = dict(stage_info.get("stage2") or {})
    bullets = [
        f"Score: `{row.get('score')}`",
        f"Stage 2 primary task type: `{stage2.get('primary_task_type')}`",
        f"Stage 2 task families: `{stage2.get('task_families')}`",
        f"Observed implementation targets: `{observed.get('implementation_targets')}`",
        f"Observed selected files: `{observed.get('selected_files')}`",
        f"Gold implementation targets: `{reference.get('implementation_targets')}`",
        f"Matched selected gold targets: `{matches.get('selected_target_hits')}`",
        f"Missed gold targets: `{matches.get('missed_targets')}`",
        f"Extra implementation targets: `{matches.get('extra_targets')}`",
    ]
    ref_targets = [str(path) for path in list(reference.get("implementation_targets") or [])]
    existing = [path for path in ref_targets if _path_exists(repo_root, path)]
    missing = [path for path in ref_targets if path not in existing]
    bullets.append(f"Gold repo-existing targets: `{existing}`")
    if missing:
        bullets.append(f"Gold non-existing targets: `{missing}`")
    return bullets


def _diagnosis_text(row: Mapping[str, Any], stage_info: Mapping[str, Any], repo_root: Path) -> str:
    classes = _failure_classes(row, stage_info, repo_root)
    parts: List[str] = []
    if "gold_reference_needs_review" in classes:
        parts.append("The downloaded gold reference points at paths that do not exist in the current repo, so the score is not trustworthy until the reference is cleaned or normalized.")
    if "scheduler_gravity" in classes:
        parts.append("The pipeline is being pulled toward `scheduled_jobs.py` and its test even when the PIS is not primarily about scheduler execution truth.")
    if "pipeline_self_reference" in classes:
        parts.append("Planning/search-plan files are being selected as if they were implementation owners, which indicates pipeline self-reference contamination.")
    if "benchmark_or_dataset_contamination" in classes:
        parts.append("Benchmark and dataset artifacts leaked into the ranked selection, which should never happen for implementation targeting.")
    if "docs_or_governance_contamination" in classes:
        parts.append("Docs or governance items were promoted into selected files instead of staying as read-only context.")
    if "mobile_gravity" in classes:
        parts.append("Mobile files are being pulled in as generic context even when the gold reference expects backend or core ownership.")
    if "missing_adjacent_tests" in classes:
        parts.append("The pipeline failed to pair likely owner files with the expected adjacent tests.")
    if not parts:
        parts.append("The main issue is owner mismatch: the selected implementation surfaces do not line up with the gold reference.")
    return " ".join(parts)


def render_analysis(row: Mapping[str, Any], stage_info: Mapping[str, Any], repo_root: Path) -> str:
    uid = str(row.get("pis_uid") or "")
    title = str(row.get("title") or uid)
    classes = _failure_classes(row, stage_info, repo_root)
    first_stage = _likely_first_failure_stage(row, stage_info, repo_root)
    lines = [
        f"# Failure Analysis: {uid}",
        "",
        f"- Title: `{title}`",
        f"- Score: `{row.get('score')}`",
        f"- Reference source: `{row.get('reference_source')}`",
        f"- Likely first obvious failure stage: `{first_stage}`",
        f"- Failure classes: `{classes}`",
        "",
        "## Diagnosis",
        _diagnosis_text(row, stage_info, repo_root),
        "",
        "## Evidence",
    ]
    for bullet in _summary_bullets(row, stage_info, repo_root):
        lines.append(f"- {bullet}")
    lines.extend(
        [
            "",
            "## Next Checks",
            "- Inspect stage-4 retrieval candidates for contamination sources if stage 5 already contains benchmark, dataset, or governance files.",
            "- Compare stage-2 family routing against the PIS title and scope to see whether the wrong subsystem bias entered before ranking.",
            "- Check why the top-ranked owner priors favored the selected hub files over the gold targets.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _sort_by_score(rows: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return sorted(rows, key=lambda row: float(row.get("score") or 0.0), reverse=True)


def _uids(rows: Iterable[Mapping[str, Any]]) -> List[str]:
    return [str(row.get("pis_uid") or "") for row in rows if str(row.get("pis_uid") or "")]


def render_root_cause_summary(
    rows: Iterable[Mapping[str, Any]],
    stage_index: Mapping[str, Mapping[str, Any]],
    repo_root: Path,
    score_json_path: Path,
    analyses_dir: Path,
) -> str:
    row_list = list(rows)
    mean_score = 0.0
    if row_list:
        mean_score = round(sum(float(row.get("score") or 0.0) for row in row_list) / len(row_list), 2)

    class_counts: Counter[str] = Counter()
    stage_counts: Counter[str] = Counter()
    class_to_rows: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    stage_to_rows: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    repo_existing_rows: List[Mapping[str, Any]] = []
    review_rows: List[Mapping[str, Any]] = []

    for row in row_list:
        uid = str(row.get("pis_uid") or "")
        stage_info = stage_index.get(uid, {})
        classes = _failure_classes(row, stage_info, repo_root)
        first_stage = _likely_first_failure_stage(row, stage_info, repo_root)
        for failure_class in classes:
            class_counts[failure_class] += 1
            class_to_rows[failure_class].append(row)
        stage_counts[first_stage] += 1
        stage_to_rows[first_stage].append(row)
        if "gold_reference_needs_review" in classes:
            review_rows.append(row)
        else:
            repo_existing_rows.append(row)

    top_rows = _sort_by_score(row_list)[:5]
    repo_existing_rows = _sort_by_score(repo_existing_rows)
    review_rows = sorted(review_rows, key=lambda row: str(row.get("pis_uid") or ""))

    lines = [
        "# Gold Large Root Cause Summary",
        "",
        f"- Source scores: [{score_json_path.name}]({score_json_path.resolve()})",
        f"- Per-PIS analyses: [failure_analyses]({analyses_dir.resolve()})",
        f"- Cases scored: `{len(row_list)}`",
        f"- Mean score: `{mean_score}`",
        "",
        "## Main Finding",
        "",
        "The current gold-large comparison is dominated by two different problems:",
        "",
        f"1. `{class_counts.get('gold_reference_needs_review', 0)}/{len(row_list)}` cases are not yet trustworthy because the downloaded GPT reference points at paths that do not exist in the current repo.",
        f"2. On the remaining cases, the pipeline is heavily biased toward `scheduled_jobs.py` and related scheduler surfaces, even when the PIS is clearly about another subsystem.",
        "",
        "That means the first cleanup step is to separate:",
        "",
        "- `reference-quality problems`",
        "- `actual pipeline selection problems`",
        "",
        "If those are not separated, a large fraction of the `0.0` scores will be misleading.",
        "",
        "## Failure Classes",
        "",
        "| Failure class | Count | Meaning |",
        "|---|---:|---|",
    ]

    meanings = {
        "scheduler_gravity": "`infra/controlplane/scheduled_jobs.py` and/or its test are being selected almost everywhere",
        "benchmark_or_dataset_contamination": "benchmark YAMLs or dataset artifacts leaked into ranked selection",
        "gold_reference_needs_review": "GPT reference names non-existent paths in the current repo",
        "pipeline_self_reference": "planning/search-plan pipeline files are being selected as implementation owners",
        "mobile_gravity": "mobile files are being added as generic context or false owners",
        "docs_or_governance_contamination": "docs, governance, or PIS files are being promoted into selected files",
        "missing_adjacent_tests": "likely owner surfaces are not paired with expected test files",
        "owner_selection_mismatch": "selected implementation surfaces do not line up with the gold reference",
    }
    for failure_class, count in class_counts.most_common():
        lines.append(f"| `{failure_class}` | {count} | {meanings.get(failure_class, '')} |")

    lines.extend(
        [
            "",
            "## First Obvious Failure Stage",
            "",
            "| Stage | Count | Interpretation |",
            "|---|---:|---|",
        ]
    )
    stage_meanings = {
        "reference_review_required": "cannot reliably diagnose pipeline until the GPT reference is normalized",
        "stage5_context_ranker": "routing may be directionally right, but ranking is promoting the wrong owners/noise",
        "stage2_task_classifier": "task-family bias appears wrong before ranking starts",
        "stage6_context_bundle": "good candidates were mixed with read-only context and then promoted incorrectly",
        "stage7_patch_plan": "selected context existed but failed to translate into plausible modify targets",
    }
    for stage_name, count in stage_counts.most_common():
        lines.append(f"| `{stage_name}` | {count} | {stage_meanings.get(stage_name, '')} |")

    lines.extend(
        [
            "",
            "## Cases Needing Gold Reference Review",
            "",
            "These should be reviewed before using their scores as decision-grade evidence:",
            "",
        ]
    )
    for uid in _uids(review_rows):
        lines.append(f"- `{uid}`")

    lines.extend(
        [
            "",
            "## Cases Most Useful For Real Pipeline Diagnosis Right Now",
            "",
            "These have repo-existing gold targets and therefore give the clearest signal about actual pipeline behavior:",
            "",
        ]
    )
    for uid in _uids(repo_existing_rows):
        lines.append(f"- `{uid}`")

    lines.extend(
        [
            "",
            "## What The Good Cases Say",
            "",
            "The only reasonably strong scores in the current run are:",
            "",
        ]
    )
    for row in top_rows:
        lines.append(f"- `{row.get('pis_uid')}`: `{row.get('score')}`")

    lines.extend(
        [
            "",
            "Even here, the pattern is not “excellent selection.” It is:",
            "",
            "- orchestration/server cases do better than most",
            "- mobile-chat recovery gets partial hits",
            "- anything outside those narrow areas collapses toward scheduler, pipeline, or benchmark noise",
            "",
            "## Recommended Next Order",
            "",
            "1. Clean the `gold_reference_needs_review` cases.",
            "2. Then focus on the cases whose first obvious failure is `stage5_context_ranker`.",
            "3. Only after that revisit the `stage2_task_classifier` cases.",
            "",
            "This order matters because:",
            "",
            "- reference cleanup removes false negatives",
            "- stage-5 fixes likely improve many cases at once",
            "- stage-2 fixes are smaller in count and should be based on cleaner evidence",
            "",
            "## Candidate Root-Cause Workstreams",
            "",
            "### 1. Scheduler gravity suppression",
            "",
            "Symptoms:",
            "",
            f"- `scheduled_jobs.py` appears in `{class_counts.get('scheduler_gravity', 0)}/{len(row_list)}` cases",
            "- often paired with `tests/control_plane/test_scheduled_jobs.py`",
            "- shows up even in truth contracts, chat persistence, bootstrap, and CLI UX work",
            "",
            "Likely action:",
            "",
            "- reduce generic scheduler owner priors",
            "- require stronger scheduler-family evidence before promoting scheduler files",
            "- penalize scheduler files when stage 2 does not explicitly classify the case as scheduler-centric",
            "",
            "### 2. Benchmark/dataset contamination hard block",
            "",
            "Symptoms:",
            "",
            "- benchmark YAMLs and `pis_datasets` files appear in selected files",
            "",
            "Likely action:",
            "",
            "- make `tests/benchmarks/pipeline/benchmark-gpt/**`",
            "- and `infra/pipeline/knowledge_drafts/pis_datasets/**`",
            "- non-editable and non-rankable for implementation targeting",
            "",
            "### 3. Pipeline self-reference demotion",
            "",
            "Symptoms:",
            "",
            "- `infra/pipeline/planning.py`",
            "- `infra/pipeline/stages/search_plan.py`",
            "",
            "are selected as owners for non-pipeline PISes",
            "",
            "Likely action:",
            "",
            "- demote pipeline-planning files unless the PIS is explicitly about retrieval, ranking, planning, or pipeline internals",
            "",
            "### 4. Governance/docs contamination cleanup",
            "",
            "Symptoms:",
            "",
            "- PIS markdown, contract docs, and ledger items promoted into selected files",
            "",
            "Likely action:",
            "",
            "- keep them as `reference_context` only",
            "- explicitly forbid them as implementation targets unless the PIS is documentation-only",
            "",
            "### 5. Adjacent-test promotion",
            "",
            "Symptoms:",
            "",
            "- owner-ish files selected without the expected tests",
            "",
            "Likely action:",
            "",
            "- when a likely owner is selected, actively look for paired tests in `tests/` or `src/test/`",
            "",
            "## Bottom Line",
            "",
            "The current evidence does not say “the pipeline is mildly off.”",
            "It says:",
            "",
            "- the gold set is partly dirty",
            "- the pipeline has a severe scheduler-gravity problem",
            "- ranking contamination is widespread",
            "- once those are removed, the true remaining owner-selection gaps can be diagnosed much more cleanly",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def generate_failure_analyses(
    *,
    repo_root: Path,
    score_json_path: Path,
    batch_source: Path,
    out_dir: Path,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(score_json_path.read_text(encoding="utf-8"))
    stage_index = extract_stage_summaries(batch_source)
    rows = list(data.get("results") or [])
    for row in rows:
        uid = str(row.get("pis_uid") or "")
        if not uid:
            continue
        analysis = render_analysis(row, stage_index.get(uid, {}), repo_root)
        (out_dir / f"{uid}__failure_analysis.md").write_text(analysis, encoding="utf-8")
    summary = render_root_cause_summary(rows, stage_index, repo_root, score_json_path, out_dir)
    summary_path = out_dir / "ROOT_CAUSE_SUMMARY.md"
    summary_path.write_text(summary, encoding="utf-8")
    return {
        "out_dir": str(out_dir.resolve()),
        "summary_path": str(summary_path.resolve()),
        "case_count": len(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate per-PIS failure analyses for gold-large benchmark cases.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--score-json", default="runtime/reports/file_selection_score_202_gold_only.json")
    parser.add_argument("--batch-zip", default="runtime/reports/go_batch_archives/go-pis-batch-2026-03-20-210825.zip")
    parser.add_argument("--out-dir", default="tests/benchmarks/pipeline/benchmark-gpt/gold_large/failure_analyses")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    score_json = repo_root / args.score_json
    batch_zip = repo_root / args.batch_zip
    out_dir = repo_root / args.out_dir
    generate_failure_analyses(
        repo_root=repo_root,
        score_json_path=score_json,
        batch_source=batch_zip,
        out_dir=out_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
