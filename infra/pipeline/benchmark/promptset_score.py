from __future__ import annotations

import argparse
import fnmatch
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import yaml


@dataclass
class BatchEntry:
    case_id: str
    title: str
    source_path: str
    report_dir: str
    summary: Dict[str, Any]


@dataclass
class PromptSetReference:
    case_id: str
    title: str
    trace_path: str
    stage1: Dict[str, Any]
    stage2: Dict[str, Any]
    stage3: Dict[str, Any]
    stage4: Dict[str, Any]
    stage5: Dict[str, Any]
    stage6: Dict[str, Any]
    stage7: Dict[str, Any]


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(text or "").strip())
    return cleaned.strip("._-") or "batch"


def _normalize_path(path: str) -> str:
    return str(path or "").strip().replace("\\", "/")


def _safe_ratio(num: int | float, den: int | float) -> float:
    if float(den) <= 0.0:
        return 0.0
    return float(num) / float(den)


def _f1(precision: float, recall: float) -> float:
    if precision <= 0.0 or recall <= 0.0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_yaml(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _unique(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _matches_pattern(path: str, pattern: str) -> bool:
    norm = _normalize_path(path)
    pat = _normalize_path(pattern)
    if not pat:
        return False
    if fnmatch.fnmatch(norm, pat):
        return True
    if pat.endswith("/**"):
        return norm.startswith(pat[:-3])
    if pat.endswith("/*"):
        return norm.startswith(pat[:-1])
    return False


def _matches_any(path: str, exact_paths: Sequence[str], patterns: Sequence[str]) -> bool:
    norm = _normalize_path(path)
    if norm in { _normalize_path(item) for item in exact_paths }:
        return True
    return any(_matches_pattern(norm, pattern) for pattern in patterns)


def _extract_manifest_from_text(blob: str) -> Dict[str, Any]:
    text = str(blob or "")
    for start in range(len(text) - 1, -1, -1):
        if text[start] != "{":
            continue
        try:
            candidate = json.loads(text[start:])
        except Exception:
            continue
        if isinstance(candidate, dict) and "entries" in candidate and "batch_id" in candidate:
            return candidate
    raise ValueError("Could not locate batch manifest JSON payload")


def _find_summary_in_batch_dir(batch_dir: Path, report_dir: str) -> Dict[str, Any]:
    report_path = Path(report_dir)
    if not report_path.is_absolute():
        if report_path.exists():
            report_path = report_path.resolve()
        else:
            report_path = (Path.cwd() / report_path).resolve()
    if not report_path.exists():
        return {}
    preferred = report_path / "deterministic" / "summary.json"
    if preferred.exists():
        return _read_json(preferred)
    matches = sorted(report_path.glob("**/summary.json"))
    return _read_json(matches[0]) if matches else {}


def _find_summary_in_zip(zf: zipfile.ZipFile, report_dir: str) -> Dict[str, Any]:
    norm_report = _normalize_path(report_dir)
    preferred = ""
    fallback = ""
    for name in zf.namelist():
        norm = _normalize_path(name)
        if norm_report not in norm or not norm.endswith("/summary.json"):
            continue
        if "/deterministic/summary.json" in norm:
            preferred = name
            break
        fallback = name
    target = preferred or fallback
    return json.loads(zf.read(target)) if target else {}


def load_batch_entries(batch_source: str | Path) -> Tuple[str, List[BatchEntry]]:
    path = Path(batch_source)
    if path.is_dir():
        manifest_path = path / "batch_manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Missing batch_manifest.json in {path}")
        try:
            manifest = _read_json(manifest_path)
        except json.JSONDecodeError:
            manifest = _extract_manifest_from_text(manifest_path.read_text(encoding="utf-8"))
        entries: List[BatchEntry] = []
        for entry in list(manifest.get("entries") or []):
            case_id = str(entry.get("item_id") or entry.get("pis_uid") or "").strip()
            if not case_id:
                continue
            entries.append(
                BatchEntry(
                    case_id=case_id,
                    title=str(entry.get("title") or "").strip(),
                    source_path=str(entry.get("item_path") or entry.get("pis_path") or "").strip(),
                    report_dir=str(entry.get("report_dir") or "").strip(),
                    summary=_find_summary_in_batch_dir(path, str(entry.get("report_dir") or "")),
                )
            )
        return str(path.resolve()), entries

    with zipfile.ZipFile(path) as zf:
        manifest_name = next(name for name in zf.namelist() if name.endswith("/batch_manifest.json"))
        manifest = json.loads(zf.read(manifest_name))
        entries = []
        for entry in list(manifest.get("entries") or []):
            case_id = str(entry.get("item_id") or entry.get("pis_uid") or "").strip()
            if not case_id:
                continue
            entries.append(
                BatchEntry(
                    case_id=case_id,
                    title=str(entry.get("title") or "").strip(),
                    source_path=str(entry.get("item_path") or entry.get("pis_path") or "").strip(),
                    report_dir=str(entry.get("report_dir") or "").strip(),
                    summary=_find_summary_in_zip(zf, str(entry.get("report_dir") or "")),
                )
            )
        return str(path.resolve()), entries


def _resolve_reference_dir(benchmark_root: str | Path, reference_dir: str = "") -> Path:
    root = Path(benchmark_root)
    if reference_dir:
        candidate = Path(reference_dir)
        return candidate if candidate.is_absolute() else (root / candidate)
    for name in ["gpt_references", "gpt_answers"]:
        candidate = root / name
        if candidate.exists():
            return candidate
    return root / "gpt_references"


def load_promptset_references(benchmark_root: str | Path, reference_dir: str = "") -> Dict[str, PromptSetReference]:
    ref_dir = _resolve_reference_dir(benchmark_root, reference_dir)
    index: Dict[str, PromptSetReference] = {}
    for trace_path in sorted(ref_dir.glob("*__reference_trace.yaml")):
        trace = _read_yaml(trace_path)
        case = dict(trace.get("case") or {})
        stage_expectations = dict(trace.get("stage_expectations") or {})
        case_id = str(case.get("id") or "").strip()
        if not case_id:
            continue
        index[case_id] = PromptSetReference(
            case_id=case_id,
            title=str(case.get("title") or "").strip(),
            trace_path=str(trace_path),
            stage1=dict(stage_expectations.get("stage1") or {}),
            stage2=dict(stage_expectations.get("stage2") or {}),
            stage3=dict(stage_expectations.get("stage3") or {}),
            stage4=dict(stage_expectations.get("stage4") or {}),
            stage5=dict(stage_expectations.get("stage5") or {}),
            stage6=dict(stage_expectations.get("stage6") or {}),
            stage7=dict(stage_expectations.get("stage7") or {}),
        )
    return index


def _intake_from_summary(summary: Mapping[str, Any]) -> Dict[str, Any]:
    return dict((summary.get("pipeline_snapshot") or {}).get("intake_assessment") or {})


def _classification_from_summary(summary: Mapping[str, Any]) -> Dict[str, Any]:
    return dict((summary.get("pipeline_snapshot") or {}).get("classification") or {})


def _search_plan_from_summary(summary: Mapping[str, Any]) -> Dict[str, Any]:
    return dict((summary.get("pipeline_snapshot") or {}).get("search_plan") or {})


def _has_stage1_data(summary: Mapping[str, Any]) -> bool:
    return bool(_intake_from_summary(summary))


def _has_stage2_data(summary: Mapping[str, Any]) -> bool:
    return bool(_classification_from_summary(summary))


def _has_stage3_data(summary: Mapping[str, Any]) -> bool:
    return bool(_search_plan_from_summary(summary))


def _has_stage7_data(summary: Mapping[str, Any]) -> bool:
    return bool(summary.get("selected_files") or summary.get("implementation_targets") or summary.get("selected_modify_files"))


def _retrieval_paths_from_summary(summary: Mapping[str, Any]) -> List[str]:
    retrieval = dict((summary.get("pipeline_snapshot") or {}).get("retrieval") or {})
    return _unique(
        _normalize_path(item.get("path") if isinstance(item, Mapping) else item)
        for item in list(retrieval.get("file_candidates") or [])
    )


def _ranking_paths_from_summary(summary: Mapping[str, Any]) -> List[str]:
    ranking = dict((summary.get("pipeline_snapshot") or {}).get("ranking") or {})
    return _unique(
        _normalize_path(item.get("path") if isinstance(item, Mapping) else item)
        for item in list(ranking.get("selected_files") or [])
    )


def _bundle_paths_from_summary(summary: Mapping[str, Any]) -> List[str]:
    bundle = dict((summary.get("pipeline_snapshot") or {}).get("bundle") or {})
    return _unique(
        _normalize_path(item.get("path") if isinstance(item, Mapping) else item)
        for item in list(bundle.get("files") or [])
    )


def _has_stage4_data(summary: Mapping[str, Any]) -> bool:
    return bool(_retrieval_paths_from_summary(summary))


def _has_stage5_data(summary: Mapping[str, Any]) -> bool:
    return bool(_ranking_paths_from_summary(summary))


def _has_stage6_data(summary: Mapping[str, Any]) -> bool:
    return bool(_bundle_paths_from_summary(summary))


def score_stage1(batch_source: str | Path, refs: Mapping[str, PromptSetReference]) -> Dict[str, Any]:
    source_label, entries = load_batch_entries(batch_source)
    results: List[Dict[str, Any]] = []
    for entry in entries:
        ref = refs.get(entry.case_id)
        if not ref or not _has_stage1_data(entry.summary):
            continue
        intake = _intake_from_summary(entry.summary)
        observed = _unique(str(item) for item in list(intake.get("task_families") or []))
        expected = _unique(str(item) for item in list(ref.stage1.get("expected_families") or []))
        accepted = _unique(str(item) for item in list(ref.stage1.get("accepted_families") or []))
        forbidden = _unique(str(item) for item in list(ref.stage1.get("forbidden_families") or []))
        expected_hits = [item for item in expected if item in observed]
        accepted_hits = [item for item in accepted if item in observed]
        forbidden_hits = [item for item in forbidden if item in observed]
        recall = _safe_ratio(len(expected_hits), len(expected))
        support = _safe_ratio(len(expected_hits) + len(accepted_hits), len(expected) + len(accepted))
        precision = _safe_ratio(len(expected_hits) + 0.5 * len(accepted_hits), len(observed))
        penalty = min(0.2 * len(forbidden_hits), 0.4)
        score = max(0.0, min(100.0, round(100.0 * ((0.45 * recall) + (0.35 * support) + (0.2 * precision) - penalty), 2)))
        accuracy = "accurate" if score >= 80.0 else "partial" if score >= 50.0 else "wrong"
        results.append(
            {
                "case_id": entry.case_id,
                "title": entry.title,
                "expected_families": expected,
                "accepted_families": accepted,
                "observed_families": observed,
                "forbidden_families": forbidden,
                "metrics": {
                    "expected_hits": expected_hits,
                    "accepted_hits": accepted_hits,
                    "forbidden_hits": forbidden_hits,
                    "recall": round(recall, 4),
                    "support": round(support, 4),
                    "precision": round(precision, 4),
                },
                "accuracy": accuracy,
                "score": score,
            }
        )
    return _aggregate_stage_results("stage1_family_detection", source_label, results)


def score_stage2(batch_source: str | Path, refs: Mapping[str, PromptSetReference]) -> Dict[str, Any]:
    source_label, entries = load_batch_entries(batch_source)
    results: List[Dict[str, Any]] = []
    for entry in entries:
        ref = refs.get(entry.case_id)
        if not ref or not _has_stage2_data(entry.summary):
            continue
        classification = _classification_from_summary(entry.summary)
        expected = str(ref.stage2.get("expected_primary") or "").strip()
        partial = _unique(str(item) for item in list(ref.stage2.get("partial_primaries") or []))
        forbidden = _unique(str(item) for item in list(ref.stage2.get("forbidden_primaries") or []))
        observed = str(classification.get("primary_task_type") or "").strip()
        observed_families = _unique(str(item) for item in list(classification.get("task_families") or []))
        if observed == expected:
            accuracy = "accurate"
            score = 100.0
        elif observed in partial:
            accuracy = "partial"
            score = 50.0
        else:
            accuracy = "wrong"
            score = 0.0
        if observed and observed in forbidden:
            score = max(0.0, score - 25.0)
            accuracy = "wrong" if score <= 0.0 else accuracy
        results.append(
            {
                "case_id": entry.case_id,
                "title": entry.title,
                "expected_primary": expected,
                "partial_primaries": partial,
                "forbidden_primaries": forbidden,
                "observed_primary": observed,
                "observed_families": observed_families,
                "accuracy": accuracy,
                "score": score,
            }
        )
    return _aggregate_stage_results("stage2_classification", source_label, results)


def score_stage3(batch_source: str | Path, refs: Mapping[str, PromptSetReference]) -> Dict[str, Any]:
    source_label, entries = load_batch_entries(batch_source)
    results: List[Dict[str, Any]] = []
    for entry in entries:
        ref = refs.get(entry.case_id)
        if not ref or not _has_stage3_data(entry.summary):
            continue
        search_plan = _search_plan_from_summary(entry.summary)
        observed = _unique(str(item) for item in list(search_plan.get("subsystem_hypotheses") or []))
        observed_top = observed[:8]
        observed_top3 = observed[:3]
        expected = _unique(str(item) for item in list(ref.stage3.get("expected_subsystems") or []))
        secondary = _unique(str(item) for item in list(ref.stage3.get("secondary_subsystems") or []))
        forbidden = _unique(str(item) for item in list(ref.stage3.get("forbidden_subsystems") or []))
        primary_hits = [item for item in expected if item in observed_top]
        secondary_hits = [item for item in secondary if item in observed_top]
        top3_hits = [item for item in expected if item in observed_top3]
        forbidden_hits = [item for item in forbidden if item in observed_top]
        primary_recall = _safe_ratio(len(primary_hits), len(expected))
        support_recall = _safe_ratio(len(primary_hits) + len(secondary_hits), len(expected) + len(secondary))
        top3_recall = _safe_ratio(len(top3_hits), min(len(expected), 3))
        precision = _safe_ratio(len(primary_hits) + 0.5 * len(secondary_hits), len(observed_top))
        penalty = min(0.12 * len(forbidden_hits), 0.36)
        score = max(0.0, min(100.0, round(100.0 * ((0.4 * primary_recall) + (0.2 * support_recall) + (0.2 * top3_recall) + (0.2 * precision) - penalty), 2)))
        accuracy = "accurate" if score >= 80.0 else "partial" if score >= 50.0 else "wrong"
        results.append(
            {
                "case_id": entry.case_id,
                "title": entry.title,
                "expected_subsystems": expected,
                "secondary_subsystems": secondary,
                "forbidden_subsystems": forbidden,
                "observed_subsystems": observed,
                "observed_top_subsystems": observed_top,
                "metrics": {
                    "primary_hits": primary_hits,
                    "secondary_hits": secondary_hits,
                    "forbidden_hits": forbidden_hits,
                    "primary_recall": round(primary_recall, 4),
                    "support_recall": round(support_recall, 4),
                    "top3_recall": round(top3_recall, 4),
                    "precision": round(precision, 4),
                },
                "accuracy": accuracy,
                "score": score,
            }
        )
    return _aggregate_stage_results("stage3_search_plan", source_label, results)


def _score_path_stage(
    *,
    mode: str,
    batch_source: str | Path,
    refs: Mapping[str, PromptSetReference],
    stage_attr: str,
    observed_getter,
    has_data,
    exact_key: str,
    pattern_key: str,
    accepted_exact_key: str = "",
    accepted_pattern_key: str = "",
) -> Dict[str, Any]:
    source_label, entries = load_batch_entries(batch_source)
    results: List[Dict[str, Any]] = []
    for entry in entries:
        ref = refs.get(entry.case_id)
        if not ref or not has_data(entry.summary):
            continue
        stage = dict(getattr(ref, stage_attr) or {})
        observed = _unique(_normalize_path(item) for item in observed_getter(entry.summary))
        observed_top = observed[:10]
        expected_exact = _unique(_normalize_path(item) for item in list(stage.get(exact_key) or []))
        expected_patterns = _unique(_normalize_path(item) for item in list(stage.get(pattern_key) or []))
        accepted_exact = _unique(_normalize_path(item) for item in list(stage.get(accepted_exact_key) or [])) if accepted_exact_key else []
        accepted_patterns = _unique(_normalize_path(item) for item in list(stage.get(accepted_pattern_key) or [])) if accepted_pattern_key else []
        forbidden_patterns = _unique(_normalize_path(item) for item in list(stage.get("forbidden_patterns") or []))

        expected_exact_hits, expected_pattern_hits = _match_expected_units(observed_top, expected_exact, expected_patterns)
        accepted_exact_hits, accepted_pattern_hits = _match_expected_units(observed_top, accepted_exact, accepted_patterns)
        matched_expected_paths = _matching_observed_paths(observed_top, expected_exact, expected_patterns)
        matched_accepted_paths = _matching_observed_paths(observed_top, accepted_exact, accepted_patterns)
        forbidden_hits = [path for path in observed_top if any(_matches_pattern(path, pat) for pat in forbidden_patterns)]

        expected_units = len(expected_exact) + len(expected_patterns)
        accepted_units = len(accepted_exact) + len(accepted_patterns)
        expected_hits = len(expected_exact_hits) + len(expected_pattern_hits)
        accepted_hits = len(accepted_exact_hits) + len(accepted_pattern_hits)
        recall = _safe_ratio(expected_hits, expected_units)
        support = _safe_ratio(expected_hits + accepted_hits, expected_units + accepted_units)
        precision = _safe_ratio(len(matched_expected_paths) + 0.5 * len(matched_accepted_paths), len(observed_top))
        penalty = min(0.15 * len(forbidden_hits), 0.45)
        score = max(0.0, min(100.0, round(100.0 * ((0.5 * recall) + (0.3 * support) + (0.2 * precision) - penalty), 2)))
        accuracy = "accurate" if score >= 80.0 else "partial" if score >= 50.0 else "wrong"

        results.append(
            {
                "case_id": entry.case_id,
                "title": entry.title,
                "expected_exact": expected_exact,
                "expected_patterns": expected_patterns,
                "accepted_exact": accepted_exact,
                "accepted_patterns": accepted_patterns,
                "observed_paths": observed,
                "observed_top_paths": observed_top,
                "accuracy": accuracy,
                "score": score,
                "metrics": {
                    "recall": round(recall, 4),
                    "support": round(support, 4),
                    "precision": round(precision, 4),
                    "forbidden_hits": forbidden_hits,
                    "expected_exact_hits": expected_exact_hits,
                    "expected_pattern_hits": expected_pattern_hits,
                    "accepted_exact_hits": accepted_exact_hits,
                    "accepted_pattern_hits": accepted_pattern_hits,
                },
            }
        )
    return _aggregate_stage_results(mode, source_label, results)


def score_stage4(batch_source: str | Path, refs: Mapping[str, PromptSetReference]) -> Dict[str, Any]:
    return _score_path_stage(
        mode="stage4_retrieval_candidates",
        batch_source=batch_source,
        refs=refs,
        stage_attr="stage4",
        observed_getter=_retrieval_paths_from_summary,
        has_data=_has_stage4_data,
        exact_key="expected_candidates",
        pattern_key="expected_candidate_patterns",
        accepted_exact_key="reference_context",
        accepted_pattern_key="reference_context_patterns",
    )


def score_stage5(batch_source: str | Path, refs: Mapping[str, PromptSetReference]) -> Dict[str, Any]:
    return _score_path_stage(
        mode="stage5_ranking",
        batch_source=batch_source,
        refs=refs,
        stage_attr="stage5",
        observed_getter=_ranking_paths_from_summary,
        has_data=_has_stage5_data,
        exact_key="expected_selected_files",
        pattern_key="expected_selected_patterns",
        accepted_exact_key="accepted_selected_files",
        accepted_pattern_key="accepted_selected_patterns",
    )


def score_stage6(batch_source: str | Path, refs: Mapping[str, PromptSetReference]) -> Dict[str, Any]:
    return _score_path_stage(
        mode="stage6_bundle",
        batch_source=batch_source,
        refs=refs,
        stage_attr="stage6",
        observed_getter=_bundle_paths_from_summary,
        has_data=_has_stage6_data,
        exact_key="expected_bundle_files",
        pattern_key="expected_bundle_patterns",
        accepted_exact_key="accepted_bundle_files",
        accepted_pattern_key="accepted_bundle_patterns",
    )


def _match_expected_units(observed_paths: Sequence[str], exact_units: Sequence[str], pattern_units: Sequence[str]) -> Tuple[List[str], List[str]]:
    exact_hits = [unit for unit in exact_units if any(_normalize_path(path) == _normalize_path(unit) for path in observed_paths)]
    pattern_hits = [unit for unit in pattern_units if any(_matches_pattern(path, unit) for path in observed_paths)]
    return exact_hits, pattern_hits


def _matching_observed_paths(observed_paths: Sequence[str], exact_units: Sequence[str], pattern_units: Sequence[str]) -> List[str]:
    return [path for path in observed_paths if _matches_any(path, exact_units, pattern_units)]


def score_stage7(batch_source: str | Path, refs: Mapping[str, PromptSetReference]) -> Dict[str, Any]:
    source_label, entries = load_batch_entries(batch_source)
    results: List[Dict[str, Any]] = []
    for entry in entries:
        ref = refs.get(entry.case_id)
        if not ref or not _has_stage7_data(entry.summary):
            continue
        selected = _unique(_normalize_path(item) for item in list(entry.summary.get("selected_files") or []))
        implementation_targets = _unique(_normalize_path(item) for item in list(entry.summary.get("implementation_targets") or []))
        if not implementation_targets:
            implementation_targets = list(selected)
        stage7 = dict(ref.stage7 or {})
        ref_targets = _unique(_normalize_path(item) for item in list(stage7.get("implementation_targets") or []))
        ref_target_patterns = _unique(_normalize_path(item) for item in list(stage7.get("implementation_target_patterns") or []))
        ref_context = _unique(_normalize_path(item) for item in list(stage7.get("reference_context") or []))
        ref_context_patterns = _unique(_normalize_path(item) for item in list(stage7.get("reference_context_patterns") or []))
        forbidden_patterns = _unique(_normalize_path(item) for item in list(stage7.get("forbidden_patterns") or []))

        target_exact_hits, target_pattern_hits = _match_expected_units(implementation_targets, ref_targets, ref_target_patterns)
        context_exact_hits, context_pattern_hits = _match_expected_units(selected, ref_context, ref_context_patterns)
        matched_target_paths = _matching_observed_paths(implementation_targets, ref_targets, ref_target_patterns)
        matched_selected_paths = _matching_observed_paths(selected, ref_targets, ref_target_patterns)
        matched_context_paths = _matching_observed_paths(selected, ref_context, ref_context_patterns)
        forbidden_selected_hits = [path for path in selected if any(_matches_pattern(path, pat) for pat in forbidden_patterns)]
        forbidden_target_hits = [path for path in implementation_targets if any(_matches_pattern(path, pat) for pat in forbidden_patterns)]

        target_unit_total = len(ref_targets) + len(ref_target_patterns)
        context_unit_total = len(ref_context) + len(ref_context_patterns)
        target_unit_hits = len(target_exact_hits) + len(target_pattern_hits)
        context_unit_hits = len(context_exact_hits) + len(context_pattern_hits)
        target_recall = _safe_ratio(target_unit_hits, target_unit_total)
        context_recall = _safe_ratio(context_unit_hits, context_unit_total)
        target_precision = _safe_ratio(len(matched_target_paths), len(implementation_targets))
        selected_precision = _safe_ratio(len(_unique(matched_selected_paths + matched_context_paths)), len(selected))
        expected_test_units = [item for item in ref_targets + ref_context if item.startswith("tests/")] + [pat for pat in ref_target_patterns + ref_context_patterns if pat.startswith("tests/")]
        test_hits = [path for path in selected if path.startswith("tests/") and (_matches_any(path, ref_targets, ref_target_patterns) or _matches_any(path, ref_context, ref_context_patterns))]
        test_recall = _safe_ratio(len(test_hits), len(expected_test_units))
        forbidden_target_ratio = _safe_ratio(len(forbidden_target_hits), len(implementation_targets))
        forbidden_selected_ratio = _safe_ratio(len(forbidden_selected_hits), len(selected))

        score = (
            35.0 * target_recall
            + 20.0 * context_recall
            + 20.0 * target_precision
            + 10.0 * selected_precision
            + 10.0 * test_recall
            + 5.0 * _safe_ratio(len(matched_context_paths), max(len(selected), 1))
        )
        score -= 20.0 * forbidden_target_ratio
        score -= 8.0 * forbidden_selected_ratio
        score = max(0.0, min(100.0, round(score, 2)))
        accuracy = "accurate" if score >= 80.0 else "partial" if score >= 50.0 else "wrong"

        results.append(
            {
                "case_id": entry.case_id,
                "title": entry.title,
                "reference_source": ref.trace_path,
                "score": score,
                "accuracy": accuracy,
                "metrics": {
                    "target_recall": round(target_recall, 4),
                    "context_recall": round(context_recall, 4),
                    "target_precision": round(target_precision, 4),
                    "selected_precision": round(selected_precision, 4),
                    "test_recall": round(test_recall, 4),
                    "forbidden_target_ratio": round(forbidden_target_ratio, 4),
                    "forbidden_selected_ratio": round(forbidden_selected_ratio, 4),
                },
                "observed": {
                    "selected_files": selected,
                    "implementation_targets": implementation_targets,
                },
                "reference": {
                    "implementation_targets": ref_targets,
                    "implementation_target_patterns": ref_target_patterns,
                    "reference_context": ref_context,
                    "reference_context_patterns": ref_context_patterns,
                    "forbidden_patterns": forbidden_patterns,
                },
                "matches": {
                    "target_exact_hits": target_exact_hits,
                    "target_pattern_hits": target_pattern_hits,
                    "context_exact_hits": context_exact_hits,
                    "context_pattern_hits": context_pattern_hits,
                    "matched_target_paths": matched_target_paths,
                    "matched_selected_paths": matched_selected_paths,
                    "matched_context_paths": matched_context_paths,
                    "test_hits": test_hits,
                    "forbidden_target_hits": forbidden_target_hits,
                    "forbidden_selected_hits": forbidden_selected_hits,
                },
            }
        )
    return _aggregate_stage_results("stage7_file_selection", source_label, results)


def _aggregate_stage_results(mode: str, source_label: str, results: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    accurate = sum(1 for item in results if item.get("accuracy") == "accurate")
    partial = sum(1 for item in results if item.get("accuracy") == "partial")
    wrong = sum(1 for item in results if item.get("accuracy") == "wrong")
    return {
        "mode": mode,
        "batch_source": source_label,
        "scored_cases": len(results),
        "exact_accuracy": round(_safe_ratio(accurate, len(results)), 4) if results else 0.0,
        "support_accuracy": round(_safe_ratio(accurate + partial, len(results)), 4) if results else 0.0,
        "mean_score": round(sum(float(item.get("score") or 0.0) for item in results) / max(len(results), 1), 2) if results else 0.0,
        "counts": {"accurate": accurate, "partial": partial, "wrong": wrong},
        "results": sorted(results, key=lambda item: (float(item.get("score") or 0.0), str(item.get("case_id") or ""))),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_md(path: Path, title: str, aggregate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], render_row) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {title}",
        "",
        f"- Source: `{aggregate.get('batch_source')}`",
        f"- Cases scored: `{aggregate.get('scored_cases')}`",
        f"- Exact accuracy: `{aggregate.get('exact_accuracy')}`",
        f"- Support accuracy: `{aggregate.get('support_accuracy')}`",
        f"- Mean score: `{aggregate.get('mean_score')}`",
        "",
    ]
    lines.extend(render_row(rows))
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _render_stage1(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    lines = ["| Case | Score | Accuracy | Expected | Observed |", "|---|---:|---|---|---|"]
    for row in rows:
        lines.append(
            "| {case_id} | {score:.2f} | {accuracy} | {expected} | {observed} |".format(
                case_id=row.get("case_id"),
                score=float(row.get("score") or 0.0),
                accuracy=row.get("accuracy"),
                expected=", ".join(row.get("expected_families") or []) or "(none)",
                observed=", ".join(row.get("observed_families") or []) or "(none)",
            )
        )
    return lines


def _render_stage2(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    lines = ["| Case | Score | Accuracy | Expected | Observed | Families |", "|---|---:|---|---|---|---|"]
    for row in rows:
        lines.append(
            "| {case_id} | {score:.2f} | {accuracy} | {expected} | {observed} | {families} |".format(
                case_id=row.get("case_id"),
                score=float(row.get("score") or 0.0),
                accuracy=row.get("accuracy"),
                expected=row.get("expected_primary") or "(none)",
                observed=row.get("observed_primary") or "(none)",
                families=", ".join(row.get("observed_families") or []) or "(none)",
            )
        )
    return lines


def _render_stage3(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    lines = ["| Case | Score | Accuracy | Expected Primary | Observed Top Subsystems |", "|---|---:|---|---|---|"]
    for row in rows:
        lines.append(
            "| {case_id} | {score:.2f} | {accuracy} | {expected} | {observed} |".format(
                case_id=row.get("case_id"),
                score=float(row.get("score") or 0.0),
                accuracy=row.get("accuracy"),
                expected=", ".join(row.get("expected_subsystems") or []) or "(none)",
                observed=", ".join(row.get("observed_top_subsystems") or []) or "(none)",
            )
        )
    return lines


def _render_stage7(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    lines = ["| Case | Score | Accuracy | Target Recall | Context Recall | Target Precision |", "|---|---:|---|---:|---:|---:|"]
    for row in rows:
        metrics = dict(row.get("metrics") or {})
        lines.append(
            "| {case_id} | {score:.2f} | {accuracy} | {target_recall:.2f} | {context_recall:.2f} | {target_precision:.2f} |".format(
                case_id=row.get("case_id"),
                score=float(row.get("score") or 0.0),
                accuracy=row.get("accuracy"),
                target_recall=float(metrics.get("target_recall") or 0.0),
                context_recall=float(metrics.get("context_recall") or 0.0),
                target_precision=float(metrics.get("target_precision") or 0.0),
            )
        )
    return lines


def _render_path_stage(rows: Sequence[Mapping[str, Any]]) -> List[str]:
    lines = ["| Case | Score | Accuracy | Recall | Support | Precision |", "|---|---:|---|---:|---:|---:|"]
    for row in rows:
        metrics = dict(row.get("metrics") or {})
        lines.append(
            "| {case_id} | {score:.2f} | {accuracy} | {recall:.2f} | {support:.2f} | {precision:.2f} |".format(
                case_id=row.get("case_id"),
                score=float(row.get("score") or 0.0),
                accuracy=row.get("accuracy"),
                recall=float(metrics.get("recall") or 0.0),
                support=float(metrics.get("support") or 0.0),
                precision=float(metrics.get("precision") or 0.0),
            )
        )
    return lines


def _default_results_dir(benchmark_root: Path) -> Path:
    return benchmark_root / "_results" / datetime.now().strftime("%Y.%m.%d-%H%M%S")


def run_all(batch_source: str | Path, benchmark_root: str | Path, out_dir: Path, reference_dir: str = "") -> Dict[str, Any]:
    refs = load_promptset_references(benchmark_root, reference_dir)
    stage_outputs = {
        "stage1": score_stage1(batch_source, refs),
        "stage2": score_stage2(batch_source, refs),
        "stage3": score_stage3(batch_source, refs),
        "stage4": score_stage4(batch_source, refs),
        "stage5": score_stage5(batch_source, refs),
        "stage6": score_stage6(batch_source, refs),
        "stage7": score_stage7(batch_source, refs),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    renderers = {
        "stage1": ("PromptSet Stage 1 Report", _render_stage1),
        "stage2": ("PromptSet Stage 2 Report", _render_stage2),
        "stage3": ("PromptSet Stage 3 Report", _render_stage3),
        "stage4": ("PromptSet Stage 4 Report", _render_path_stage),
        "stage5": ("PromptSet Stage 5 Report", _render_path_stage),
        "stage6": ("PromptSet Stage 6 Report", _render_path_stage),
        "stage7": ("PromptSet Stage 7 Report", _render_stage7),
    }
    for stage, aggregate in stage_outputs.items():
        label = _slug(Path(str(batch_source)).stem if Path(str(batch_source)).suffix else Path(str(batch_source)).name)
        _write_json(out_dir / f"{stage}_score_{label}.json", aggregate)
        title, renderer = renderers[stage]
        _write_md(out_dir / f"{stage}_score_{label}.md", title, aggregate, list(aggregate.get("results") or []), renderer)
    report = {
        "benchmark_root": str(Path(benchmark_root).resolve()),
        "batch_source": str(Path(batch_source).resolve()),
        "out_dir": str(out_dir.resolve()),
        "stages": stage_outputs,
    }
    _write_json(out_dir / "reference_trace_score_report.json", report)
    _write_json(out_dir / "promptset_score_report.json", report)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score reference-trace benchmark batches at stages 1 through 7 using GPT reference traces.")
    parser.add_argument("batch_source", help="PromptSet batch directory or zip archive")
    parser.add_argument("--benchmark-root", default="tests/benchmarks/pipeline/benchmark-gpt/promptSet")
    parser.add_argument("--reference-dir", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    benchmark_root = Path(args.benchmark_root)
    out_dir = Path(args.out_dir) if args.out_dir else _default_results_dir(benchmark_root)
    report = run_all(args.batch_source, benchmark_root, out_dir, args.reference_dir)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"out_dir={report['out_dir']}")
        for stage, aggregate in report["stages"].items():
            print(f"{stage}_scored_cases={aggregate['scored_cases']}")
            print(f"{stage}_mean_score={aggregate['mean_score']}")
            print(f"{stage}_exact_accuracy={aggregate['exact_accuracy']}")
            print(f"{stage}_support_accuracy={aggregate['support_accuracy']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
