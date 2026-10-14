from __future__ import annotations

import argparse
import json
import statistics
import zipfile
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _norm(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def _canonical_equivalent_paths(value: Any) -> List[str]:
    text = _norm(value).strip("/")
    if not text:
        return []
    variants = {text}
    if text.startswith("tools/webui/"):
        variants.add("infra/webui/" + text[len("tools/webui/") :])
    elif text.startswith("infra/webui/"):
        variants.add("tools/webui/" + text[len("infra/webui/") :])
    if text.startswith("tools/controlplane/"):
        variants.add("infra/controlplane/" + text[len("tools/controlplane/") :])
    elif text.startswith("infra/controlplane/"):
        variants.add("tools/controlplane/" + text[len("infra/controlplane/") :])
    if text == "tools/webui/server_routes_and_main.py":
        variants.add("infra/webui/server_routes_and_main_parts/part05__webuirequesthandler.py")
    if text == "infra/webui/server_routes_and_main_parts/part05__webuirequesthandler.py":
        variants.add("tools/webui/server_routes_and_main.py")
    return sorted(variants)


def _slug_stage(stage_name: str) -> str:
    return str(stage_name or "").replace("_", "")


def _safe_mean(values: Sequence[float]) -> float:
    return round(float(statistics.mean(values)), 2) if values else 0.0


def _set(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in list(values or []):
        for text in _canonical_equivalent_paths(value):
            if not text or text in seen:
                continue
            out.append(text)
            seen.add(text)
    return out


def _match_patterns(paths: Iterable[str], patterns: Iterable[str]) -> List[str]:
    norm_paths = _set(paths)
    norm_patterns = _set(patterns)
    out: List[str] = []
    for path in norm_paths:
        if any(fnmatch(path, pattern) for pattern in norm_patterns):
            out.append(path)
    return out


def _f1(expected: Sequence[str], observed: Sequence[str]) -> tuple[float, float, float]:
    expected_set = set(_set(expected))
    observed_set = set(_set(observed))
    if not expected_set and not observed_set:
        return (1.0, 1.0, 1.0)
    if not expected_set:
        return (0.0, 1.0 if not observed_set else 0.0, 0.0)
    hits = len(expected_set & observed_set)
    precision = float(hits) / float(len(observed_set)) if observed_set else 0.0
    recall = float(hits) / float(len(expected_set)) if expected_set else 0.0
    if precision + recall <= 0:
        return (precision, recall, 0.0)
    return (precision, recall, (2.0 * precision * recall) / (precision + recall))


def _recall(expected: Sequence[str], observed: Sequence[str]) -> float:
    expected_set = set(_set(expected))
    if not expected_set:
        return 1.0
    observed_set = set(_set(observed))
    return float(len(expected_set & observed_set)) / float(len(expected_set))


def _extract_strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        out: List[str] = []
        for key in ("path", "path_or_pattern", "name", "subsystem", "value"):
            text = _norm(value.get(key))
            if text:
                out.append(text)
        return out
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(_extract_strings(item))
        return _set(out)
    return []


def _load_reference_cases(gpt_answers_dir: Path) -> Dict[str, Dict[str, Any]]:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required to read GPT answer YAML files") from exc

    refs: Dict[str, Dict[str, Any]] = {}
    for path in sorted(gpt_answers_dir.glob("*__reference_trace.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        case = dict(data.get("case") or {})
        uid = _norm(case.get("id") or path.stem.replace("__reference_trace", ""))
        if not uid:
            continue
        refs[uid] = {
            "uid": uid,
            "title": _norm(case.get("title") or uid),
            "path": str(path),
            "raw": data,
            "stage_expectations": dict(data.get("stage_expectations") or {}),
            "candidate_files": dict(data.get("candidate_files") or {}),
        }
    return refs


class BatchArchiveReader:
    def __init__(self, batch_source: Path) -> None:
        self.batch_source = batch_source.resolve()
        self.is_zip = self.batch_source.is_file()
        if self.is_zip:
            self.zf = zipfile.ZipFile(self.batch_source)
            self.names = set(self.zf.namelist())
        else:
            self.zf = None
            self.names = set()

    def close(self) -> None:
        if self.zf is not None:
            self.zf.close()

    def _read_json_zip(self, name: str) -> Dict[str, Any]:
        assert self.zf is not None
        return json.loads(self.zf.read(name))

    def _read_json_path(self, path: Path) -> Dict[str, Any]:
        return _load_json(path)

    def load_manifest(self) -> Dict[str, Any]:
        if self.is_zip:
            manifest_name = next(name for name in self.names if name.endswith("/batch_manifest.json"))
            return self._read_json_zip(manifest_name)
        return _load_json(self.batch_source / "batch_manifest.json")

    def find_run_prefix(self, run_id: str) -> str:
        needle = f"/runs/{run_id}/01_job_contract.json"
        if self.is_zip:
            for name in self.names:
                if needle in name:
                    return name.rsplit("/runs/", 1)[0]
            return ""
        for path in self.batch_source.rglob("01_job_contract.json"):
            parts = path.as_posix()
            if needle in parts:
                return str(path.parent.parent)
        return ""

    def read_stage_json(self, prefix: str, run_id: str, filename: str) -> Optional[Dict[str, Any]]:
        if not prefix:
            return None
        if self.is_zip:
            name = f"{prefix}/runs/{run_id}/{filename}"
            if name not in self.names:
                return None
            return self._read_json_zip(name)
        path = Path(prefix) / "runs" / run_id / filename
        if not path.exists():
            return None
        return self._read_json_path(path)

    def read_summary_json(self, report_dir: str) -> Optional[Dict[str, Any]]:
        report_dir = _norm(report_dir)
        if not report_dir:
            return None
        if self.is_zip:
            name = f"{report_dir}/deterministic/summary.json"
            if name not in self.names:
                return None
            return self._read_json_zip(name)
        path = self.batch_source / report_dir / "deterministic" / "summary.json"
        if not path.exists():
            alt = Path(report_dir) / "deterministic" / "summary.json"
            if alt.exists():
                path = alt
            else:
                return None
        return self._read_json_path(path)


def _extract_observed(batch_source: Path) -> Dict[str, Dict[str, Any]]:
    reader = BatchArchiveReader(batch_source)
    try:
        manifest = reader.load_manifest()
        observed: Dict[str, Dict[str, Any]] = {}
        for entry in list(manifest.get("entries") or []):
            uid = _norm(entry.get("pis_uid") or entry.get("item_id"))
            if not uid:
                continue
            run_ids = [text for text in _set(entry.get("run_ids") or []) if text]
            run_id = run_ids[0] if run_ids else ""
            report_dir = _norm(entry.get("report_dir"))
            summary = reader.read_summary_json(report_dir)
            prefix = reader.find_run_prefix(run_id) if run_id else ""
            stage2 = reader.read_stage_json(prefix, run_id, "02_task_profile.json") if run_id else None
            stage3 = reader.read_stage_json(prefix, run_id, "03_search_plan.json") if run_id else None
            stage4 = reader.read_stage_json(prefix, run_id, "04_retrieval.json") if run_id else None
            stage5 = reader.read_stage_json(prefix, run_id, "05_context_ranked.json") if run_id else None
            stage6 = reader.read_stage_json(prefix, run_id, "06_context_bundle.json") if run_id else None
            stage7 = reader.read_stage_json(prefix, run_id, "07_patch_plan.json") if run_id else None
            observed[uid] = {
                "pis_uid": uid,
                "title": _norm(entry.get("title") or uid),
                "entry": dict(entry),
                "summary": summary or {},
                "stage2": stage2 or {},
                "stage3": stage3 or {},
                "stage4": stage4 or {},
                "stage5": stage5 or {},
                "stage6": stage6 or {},
                "stage7": stage7 or {},
            }
        return observed
    finally:
        reader.close()


def _extract_stage1_observed(obs: Mapping[str, Any]) -> Dict[str, Any]:
    task_profile = dict((dict(obs.get("stage2") or {}).get("result") or {}).get("task_profile") or {})
    return {"families": _set(task_profile.get("task_families") or [])}


def _extract_stage2_observed(obs: Mapping[str, Any]) -> Dict[str, Any]:
    task_profile = dict((dict(obs.get("stage2") or {}).get("result") or {}).get("task_profile") or {})
    return {
        "primary_task_type": _norm(task_profile.get("primary_task_type")),
        "task_families": _set(task_profile.get("task_families") or []),
    }


def _extract_stage3_observed(obs: Mapping[str, Any]) -> Dict[str, Any]:
    search_plan = dict((dict(obs.get("stage3") or {}).get("result") or {}).get("search_plan") or {})
    target_subsystems = search_plan.get("subsystem_hypotheses")
    if not target_subsystems:
        target_subsystems = search_plan.get("target_subsystems") or []
    return {
        "target_subsystems": _set(target_subsystems),
        "queries": list(search_plan.get("queries") or []),
    }


def _extract_stage4_observed(obs: Mapping[str, Any]) -> Dict[str, Any]:
    retrieval = dict((dict(obs.get("stage4") or {}).get("result") or {}).get("retrieval") or {})
    candidates = list(retrieval.get("file_candidates") or [])
    paths = [_norm(item.get("path")) for item in candidates if _norm(item.get("path"))]
    return {
        "candidate_files": paths,
        "top_6_candidates": paths[:6],
        "top_20_candidates": paths[:20],
    }


def _extract_stage5_observed(obs: Mapping[str, Any]) -> Dict[str, Any]:
    summary = dict(obs.get("summary") or {})
    return {
        "selected_files": _set(summary.get("selected_files") or []),
        "implementation_targets": _set(summary.get("implementation_targets") or []),
    }


def _extract_stage6_observed(obs: Mapping[str, Any]) -> Dict[str, Any]:
    bundle = dict((dict(obs.get("stage6") or {}).get("result") or {}).get("context_bundle") or {})
    diagnostics = dict(bundle.get("bundle_diagnostics") or {})
    return {
        "bundle_files": _set(diagnostics.get("bundle_paths") or []),
        "bundle_quality_status": _norm(bundle.get("bundle_quality_status")),
    }


def _extract_stage7_observed(obs: Mapping[str, Any]) -> Dict[str, Any]:
    summary = dict(obs.get("summary") or {})
    return {
        "selected_files": _set(summary.get("selected_files") or []),
        "implementation_targets": _set(summary.get("implementation_targets") or []),
        "reference_context": _set(summary.get("reference_context") or []),
        "selected_modify_files": _set(summary.get("selected_modify_files") or []),
    }


def _stage_score(stage_name: str, ref: Mapping[str, Any], obs: Mapping[str, Any]) -> Dict[str, Any]:
    stage_expectations = dict(ref.get("stage_expectations") or {})
    stage_ref = dict(stage_expectations.get(stage_name) or {})
    title = _norm(ref.get("title"))
    uid = _norm(ref.get("uid"))

    if stage_name == "stage1":
        observed = _extract_stage1_observed(obs)
        expected = _set(stage_ref.get("expected_families") or [])
        forbidden = _set(stage_ref.get("forbidden_families") or [])
        observed_families = observed["families"]
        expected_recall = _recall(expected, observed_families)
        forbidden_hits = sorted(set(observed_families) & set(forbidden))
        score = round(100.0 * ((0.8 * expected_recall) + (0.2 * (0.0 if forbidden_hits else 1.0))), 2)
        return {
            "pis_uid": uid,
            "title": title,
            "score": score,
            "reference_source": str(ref.get("path") or ""),
            "observed": observed,
            "reference": {"expected_families": expected, "forbidden_families": forbidden},
            "matches": {
                "expected_hits": sorted(set(expected) & set(observed_families)),
                "missed_expected": sorted(set(expected) - set(observed_families)),
                "forbidden_hits": forbidden_hits,
            },
        }

    if stage_name == "stage2":
        observed = _extract_stage2_observed(obs)
        expected_primary = _norm(stage_ref.get("expected_primary"))
        partial = _set(stage_ref.get("partial_primaries") or [])
        forbidden = _set(stage_ref.get("forbidden_primaries") or [])
        actual = _norm(observed.get("primary_task_type"))
        if actual == expected_primary:
            score = 100.0
        elif actual in partial:
            score = 65.0
        elif actual in forbidden or not actual:
            score = 0.0
        else:
            score = 20.0
        return {
            "pis_uid": uid,
            "title": title,
            "score": round(score, 2),
            "reference_source": str(ref.get("path") or ""),
            "observed": observed,
            "reference": {
                "expected_primary": expected_primary,
                "partial_primaries": partial,
                "forbidden_primaries": forbidden,
            },
            "matches": {
                "expected_match": actual == expected_primary,
                "partial_match": actual in partial,
                "forbidden_match": actual in forbidden,
            },
        }

    if stage_name == "stage3":
        observed = _extract_stage3_observed(obs)
        expected = _set(stage_ref.get("expected_subsystems") or [])
        secondary = _set(stage_ref.get("secondary_subsystems") or [])
        forbidden = _set(stage_ref.get("forbidden_subsystems") or [])
        observed_subsystems = observed["target_subsystems"]
        score = 100.0 * (
            (0.7 * _recall(expected, observed_subsystems)) +
            (0.2 * _recall(secondary, observed_subsystems)) +
            (0.1 * (0.0 if sorted(set(observed_subsystems) & set(forbidden)) else 1.0))
        )
        return {
            "pis_uid": uid,
            "title": title,
            "score": round(score, 2),
            "reference_source": str(ref.get("path") or ""),
            "observed": observed,
            "reference": {
                "expected_subsystems": expected,
                "secondary_subsystems": secondary,
                "forbidden_subsystems": forbidden,
            },
            "matches": {
                "expected_hits": sorted(set(expected) & set(observed_subsystems)),
                "secondary_hits": sorted(set(secondary) & set(observed_subsystems)),
                "forbidden_hits": sorted(set(forbidden) & set(observed_subsystems)),
            },
        }

    if stage_name == "stage4":
        observed = _extract_stage4_observed(obs)
        expected = _set(stage_ref.get("expected_candidates") or [])
        expected += _match_patterns(observed["candidate_files"], _set(stage_ref.get("expected_candidate_patterns") or []))
        expected = _set(expected)
        reference_context = _set(stage_ref.get("reference_context") or [])
        reference_context += _match_patterns(observed["candidate_files"], _set(stage_ref.get("reference_context_patterns") or []))
        reference_context = _set(reference_context)
        forbidden_hits = _match_patterns(observed["candidate_files"], _set(stage_ref.get("forbidden_patterns") or []))
        score = 100.0 * (
            (0.45 * _recall(expected, observed["candidate_files"])) +
            (0.35 * _recall(expected, observed["top_6_candidates"])) +
            (0.1 * _recall(reference_context, observed["candidate_files"])) +
            (0.1 * (0.0 if forbidden_hits else 1.0))
        )
        return {
            "pis_uid": uid,
            "title": title,
            "score": round(score, 2),
            "reference_source": str(ref.get("path") or ""),
            "observed": observed,
            "reference": {
                "expected_candidates": expected,
                "reference_context": reference_context,
                "forbidden_patterns": _set(stage_ref.get("forbidden_patterns") or []),
            },
            "matches": {
                "expected_hits": sorted(set(expected) & set(observed["candidate_files"])),
                "top_6_hits": sorted(set(expected) & set(observed["top_6_candidates"])),
                "reference_hits": sorted(set(reference_context) & set(observed["candidate_files"])),
                "forbidden_hits": forbidden_hits,
            },
        }

    if stage_name == "stage5":
        observed = _extract_stage5_observed(obs)
        expected = _set(stage_ref.get("expected_selected_files") or [])
        expected += _match_patterns(observed["selected_files"], _set(stage_ref.get("expected_selected_patterns") or []))
        expected = _set(expected)
        accepted = _set(stage_ref.get("accepted_selected_files") or [])
        accepted += _match_patterns(observed["selected_files"], _set(stage_ref.get("accepted_selected_patterns") or []))
        accepted = _set(accepted)
        forbidden_hits = _match_patterns(observed["selected_files"], _set(stage_ref.get("forbidden_patterns") or []))
        _, _, expected_f1 = _f1(expected, observed["selected_files"])
        score = 100.0 * (
            (0.75 * expected_f1) +
            (0.15 * _recall(accepted, observed["selected_files"])) +
            (0.1 * (0.0 if forbidden_hits else 1.0))
        )
        return {
            "pis_uid": uid,
            "title": title,
            "score": round(score, 2),
            "reference_source": str(ref.get("path") or ""),
            "observed": observed,
            "reference": {
                "expected_selected_files": expected,
                "accepted_selected_files": accepted,
                "forbidden_patterns": _set(stage_ref.get("forbidden_patterns") or []),
            },
            "matches": {
                "selected_hits": sorted(set(expected) & set(observed["selected_files"])),
                "missed_selected": sorted(set(expected) - set(observed["selected_files"])),
                "accepted_hits": sorted(set(accepted) & set(observed["selected_files"])),
                "forbidden_hits": forbidden_hits,
            },
        }

    if stage_name == "stage6":
        observed = _extract_stage6_observed(obs)
        expected = _set(stage_ref.get("expected_bundle_files") or [])
        expected += _match_patterns(observed["bundle_files"], _set(stage_ref.get("expected_bundle_patterns") or []))
        expected = _set(expected)
        accepted = _set(stage_ref.get("accepted_bundle_files") or [])
        accepted += _match_patterns(observed["bundle_files"], _set(stage_ref.get("accepted_bundle_patterns") or []))
        accepted = _set(accepted)
        forbidden_hits = _match_patterns(observed["bundle_files"], _set(stage_ref.get("forbidden_patterns") or []))
        _, _, expected_f1 = _f1(expected, observed["bundle_files"])
        score = 100.0 * (
            (0.75 * expected_f1) +
            (0.15 * _recall(accepted, observed["bundle_files"])) +
            (0.1 * (0.0 if forbidden_hits else 1.0))
        )
        return {
            "pis_uid": uid,
            "title": title,
            "score": round(score, 2),
            "reference_source": str(ref.get("path") or ""),
            "observed": observed,
            "reference": {
                "expected_bundle_files": expected,
                "accepted_bundle_files": accepted,
                "forbidden_patterns": _set(stage_ref.get("forbidden_patterns") or []),
            },
            "matches": {
                "bundle_hits": sorted(set(expected) & set(observed["bundle_files"])),
                "missed_bundle": sorted(set(expected) - set(observed["bundle_files"])),
                "accepted_hits": sorted(set(accepted) & set(observed["bundle_files"])),
                "forbidden_hits": forbidden_hits,
            },
        }

    observed = _extract_stage7_observed(obs)
    expected_impl = _set(stage_ref.get("implementation_targets") or [])
    expected_impl += _match_patterns(observed["implementation_targets"], _set(stage_ref.get("implementation_target_patterns") or []))
    expected_impl = _set(expected_impl)
    reference_context = _set(stage_ref.get("reference_context") or [])
    reference_context += _match_patterns(observed["reference_context"], _set(stage_ref.get("reference_context_patterns") or []))
    reference_context = _set(reference_context)
    forbidden_hits = _match_patterns(observed["selected_files"], _set(stage_ref.get("forbidden_patterns") or []))
    selected_hits = sorted(set(expected_impl) & set(observed["selected_files"]))
    missed_targets = sorted(set(expected_impl) - set(observed["implementation_targets"]))
    extra_targets = sorted(set(observed["implementation_targets"]) - set(expected_impl))
    _, _, target_f1 = _f1(expected_impl, observed["implementation_targets"])
    selected_recall = _recall(expected_impl, observed["selected_files"])
    context_recall = _recall(reference_context, observed["reference_context"] + observed["selected_files"])
    score = 100.0 * (
        (0.65 * target_f1) +
        (0.2 * selected_recall) +
        (0.1 * context_recall) +
        (0.05 * (0.0 if forbidden_hits else 1.0))
    )
    return {
        "pis_uid": uid,
        "title": title,
        "score": round(score, 2),
        "reference_source": str(ref.get("path") or ""),
        "observed": observed,
        "reference": {
            "implementation_targets": expected_impl,
            "reference_context": reference_context,
            "forbidden_patterns": _set(stage_ref.get("forbidden_patterns") or []),
        },
        "matches": {
            "selected_target_hits": selected_hits,
            "missed_targets": missed_targets,
            "extra_targets": extra_targets,
            "reference_hits": sorted(set(reference_context) & set(observed["reference_context"] + observed["selected_files"])),
            "forbidden_hits": forbidden_hits,
            "test_hits": [path for path in selected_hits if "/test" in path or path.startswith("tests/")],
        },
    }


def _available_stages(observed_by_uid: Mapping[str, Mapping[str, Any]]) -> List[str]:
    max_stage = 0
    for obs in observed_by_uid.values():
        if dict(obs.get("summary") or {}).get("implementation_targets"):
            max_stage = max(max_stage, 7)
        elif dict((dict(obs.get("stage6") or {}).get("result") or {}).get("context_bundle") or {}):
            max_stage = max(max_stage, 6)
        elif dict((dict(obs.get("stage5") or {}).get("result") or {}).get("ranked_context") or {}):
            max_stage = max(max_stage, 5)
        elif dict((dict(obs.get("stage4") or {}).get("result") or {}).get("retrieval") or {}):
            max_stage = max(max_stage, 4)
        elif dict((dict(obs.get("stage3") or {}).get("result") or {}).get("search_plan") or {}):
            max_stage = max(max_stage, 3)
        elif dict((dict(obs.get("stage2") or {}).get("result") or {}).get("task_profile") or {}):
            max_stage = max(max_stage, 2)
    max_stage = max(max_stage, 2)
    return [f"stage{number}" for number in range(1, max_stage + 1)]


def _render_stage_markdown(stage_name: str, results: Sequence[Mapping[str, Any]], mean_score: float) -> str:
    lines = [
        f"# {stage_name.upper()} Score Report",
        "",
        f"- Cases scored: `{len(results)}`",
        f"- Mean score: `{mean_score}`",
        "",
        "| PIS | Score | Hits | Misses |",
        "|---|---:|---|---|",
    ]
    for row in sorted(results, key=lambda item: float(item.get("score") or 0.0), reverse=True):
        matches = dict(row.get("matches") or {})
        hits = (
            matches.get("expected_hits")
            or matches.get("selected_target_hits")
            or matches.get("selected_hits")
            or matches.get("bundle_hits")
            or matches.get("top_6_hits")
            or []
        )
        misses = (
            matches.get("missed_expected")
            or matches.get("missed_targets")
            or matches.get("missed_selected")
            or matches.get("missed_bundle")
            or []
        )
        lines.append(
            f"| `{row.get('pis_uid')}` | `{row.get('score')}` | {', '.join(f'`{item}`' for item in list(hits)[:3]) or '-'} | {', '.join(f'`{item}`' for item in list(misses)[:3]) or '-'} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _write_stage_report(out_dir: Path, stage_name: str, batch_source: Path, results: List[Dict[str, Any]]) -> Dict[str, Any]:
    mean_score = _safe_mean([float(row.get("score") or 0.0) for row in results])
    batch_label = batch_source.name.replace(".zip", "")
    score_json = out_dir / f"{stage_name}_score_{batch_label}.json"
    score_md = out_dir / f"{stage_name}_score_{batch_label}.md"
    payload = {
        "score_stage": stage_name,
        "batch_source": str(batch_source),
        "mean_score": mean_score,
        "scored_cases": len(results),
        "results": results,
    }
    score_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    score_md.write_text(_render_stage_markdown(stage_name, results, mean_score), encoding="utf-8")
    return {
        "mean_score": mean_score,
        "scored_cases": len(results),
        "score_json": str(score_json),
        "score_md": str(score_md),
    }


def run_full_analysis(
    *,
    repo_root: Path,
    batch_source: str,
    benchmark_root: str,
    out_dir: Path,
    score_stage: str = "auto",
    allow_pis_fallback: bool = False,
    compare_batch: str = "",
    skip_failure_analyses: bool = False,
) -> Dict[str, Any]:
    del repo_root, allow_pis_fallback, compare_batch, skip_failure_analyses
    out_dir.mkdir(parents=True, exist_ok=True)
    benchmark_root_path = Path(benchmark_root).resolve()
    refs = _load_reference_cases(benchmark_root_path / "gpt_answers")
    observed_by_uid = _extract_observed(Path(batch_source))

    stages = _available_stages(observed_by_uid) if score_stage == "auto" else [str(score_stage)]
    stage_reports: Dict[str, Any] = {}
    runs: List[Dict[str, Any]] = []
    for stage_name in stages:
        results: List[Dict[str, Any]] = []
        for uid, ref in refs.items():
            obs = observed_by_uid.get(uid)
            if not obs:
                continue
            results.append(_stage_score(stage_name, ref, obs))
        report = _write_stage_report(out_dir, stage_name, Path(batch_source), results)
        stage_reports[stage_name] = report
        runs.append({"label": stage_name, "mean_score": report["mean_score"], "scored_cases": report["scored_cases"]})

    summary = {
        "out_dir": str(out_dir),
        "runs": runs,
        "stages": stage_reports,
        "diagnostics": {
            "reference_cases": len(refs),
            "observed_cases": len(observed_by_uid),
            "manifest_has_enriched_sidecar": False,
            "cases": len(refs),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score a gold-large benchmark batch against GPT stage expectations.")
    parser.add_argument("batch_source")
    parser.add_argument("--benchmark-root", default="tests/benchmarks/pipeline/benchmark-gpt/gold_large")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--score-stage", default="auto")
    parser.add_argument("--allow-pis-fallback", action="store_true")
    parser.add_argument("--compare-batch", default="")
    parser.add_argument("--skip-failure-analyses", action="store_true")
    args = parser.parse_args(argv)

    batch_source = Path(args.batch_source).resolve()
    benchmark_root = Path(args.benchmark_root).resolve()
    if not batch_source.exists():
        raise SystemExit(f"batch source not found: {batch_source}")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (benchmark_root / "_results" / batch_source.stem / "scored")
    report = run_full_analysis(
        repo_root=Path(".").resolve(),
        batch_source=str(batch_source),
        benchmark_root=str(benchmark_root),
        out_dir=out_dir,
        score_stage=args.score_stage,
        allow_pis_fallback=bool(args.allow_pis_fallback),
        compare_batch=str(args.compare_batch or ""),
        skip_failure_analyses=bool(args.skip_failure_analyses),
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
