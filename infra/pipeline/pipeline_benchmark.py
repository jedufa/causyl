from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import yaml

if __package__ in {None, ""}:
    REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.core import ensure_directory, save_json_atomic, save_text_atomic, utc_now_iso
from infra.pipeline.go_pis_batch import GoPisBatchError, run_pis_batch


DEFAULT_BENCHMARK_CONFIG = "tests/benchmarks/pipeline/pipeline_benchmark.yaml"


class PipelineBenchmarkError(RuntimeError):
    pass


def _load_execution_benchmark_api():
    from infra.pipeline.execution_benchmark import ExecutionBenchmarkError, list_execution_cases, run_execution_benchmark

    return ExecutionBenchmarkError, list_execution_cases, run_execution_benchmark


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _read_yaml(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _suite_key(suite: Mapping[str, Any]) -> str:
    return str(suite.get("key") or suite.get("name") or "").strip()


def _merged_suite_config(runner_cfg: Mapping[str, Any], suite_cfg: Mapping[str, Any]) -> Dict[str, Any]:
    merged = dict(_mapping(runner_cfg.get("defaults")))
    merged.update(dict(suite_cfg))
    return merged


def _resolve_path(repo_root: Path, raw: str | Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (repo_root / path).resolve()


def _mapping_str_str(value: Any) -> Dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    out: Dict[str, str] = {}
    for key, raw in value.items():
        key_text = str(key).strip()
        if not key_text or raw is None:
            continue
        out[key_text] = str(raw)
    return out


def _is_default_config_path(config_path: str | Path) -> bool:
    candidate = Path(str(config_path))
    return candidate.as_posix() == Path(DEFAULT_BENCHMARK_CONFIG).as_posix()


def _candidate_benchmark_roots(repo_root: Path) -> List[Path]:
    parent = repo_root.resolve().parent
    candidates: List[Path] = []
    for child in sorted(parent.iterdir()):
        if child == repo_root or not child.is_dir():
            continue
        candidates.append(child.resolve())
    return candidates


def _maybe_bootstrap_default_benchmark_assets(repo_root: Path, config_path: str | Path) -> None:
    if not _is_default_config_path(config_path):
        return
    config_target = _resolve_path(repo_root, config_path)
    benchmark_dir = config_target.parent
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    target_benchmark_gpt = benchmark_dir / "benchmark-gpt"
    for candidate_root in _candidate_benchmark_roots(repo_root):
        source_config = candidate_root / DEFAULT_BENCHMARK_CONFIG
        source_benchmark_gpt = candidate_root / "tests" / "benchmarks" / "pipeline" / "benchmark-gpt"
        if not config_target.exists() and not source_config.exists():
            continue
        if not config_target.exists():
            config_target.symlink_to(source_config.resolve())
        if source_benchmark_gpt.exists() and not target_benchmark_gpt.exists():
            target_benchmark_gpt.symlink_to(source_benchmark_gpt.resolve(), target_is_directory=True)
        if config_target.exists() and target_benchmark_gpt.exists():
            return


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(float(seconds or 0.0))))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _print_progress(message: str) -> None:
    print(f"[pipeline-benchmark] {message}", flush=True)


def _add_path_to_zip(zf: zipfile.ZipFile, source: Path, arc_prefix: str, added: set[str]) -> None:
    if not source.exists():
        return
    if source.is_file():
        arcname = f"{arc_prefix}/{source.name}" if arc_prefix else source.name
        if arcname in added:
            return
        zf.write(source, arcname)
        added.add(arcname)
        return
    for root, _, files in os.walk(source):
        root_path = Path(root)
        rel_root = root_path.relative_to(source)
        for name in sorted(files):
            file_path = root_path / name
            rel_file = rel_root / name
            arcname = str(Path(arc_prefix) / rel_file) if arc_prefix else str(rel_file)
            if arcname in added:
                continue
            zf.write(file_path, arcname)
            added.add(arcname)


def _bundle_mode(report: Mapping[str, Any]) -> str:
    raw = str(report.get("bundle_mode") or "").strip().lower()
    return raw if raw in {"slim", "full"} else "slim"


def _create_share_bundle(*, combined_root: Path, report: Mapping[str, Any], suite_reports: Sequence[Mapping[str, Any]]) -> Path:
    bundle_path = combined_root.parent / f"pipeline_benchmark_{combined_root.name}.zip"
    bundle_mode = _bundle_mode(report)
    manifest = {
        "run_id": str(report.get("run_id") or combined_root.name),
        "status": str(report.get("status") or ""),
        "created_at": utc_now_iso(),
        "combined_root": str(combined_root),
        "bundle_mode": bundle_mode,
        "included_suites": [str(suite.get("suite_key") or "") for suite in suite_reports if str(suite.get("suite_key") or "").strip()],
        "suite_result_roots": {str(suite.get("suite_key") or ""): str(suite.get("result_root") or "") for suite in suite_reports},
    }
    added: set[str] = set()
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        report_json = combined_root / "pipeline_benchmark_report.json"
        report_md = combined_root / "pipeline_benchmark_report.md"
        _add_path_to_zip(zf, report_json, combined_root.name, added)
        _add_path_to_zip(zf, report_md, combined_root.name, added)
        for suite in suite_reports:
            suite_key = str(suite.get("suite_key") or "").strip()
            suite_root = Path(str(suite.get("result_root") or "")).resolve() if str(suite.get("result_root") or "").strip() else None
            if not suite_key or suite_root is None:
                continue
            suite_prefix = f"{combined_root.name}/{suite_key}"
            scored_root = suite_root / "scored"
            if scored_root.exists():
                _add_path_to_zip(zf, scored_root, f"{suite_prefix}/scored", added)
            archive_root = suite_root / "go_batch_archives"
            batch_archive = Path(str(suite.get("batch_archive") or "")).resolve() if str(suite.get("batch_archive") or "").strip() else None
            if bundle_mode == "full":
                if archive_root.exists():
                    _add_path_to_zip(zf, archive_root, f"{suite_prefix}/go_batch_archives", added)
            elif batch_archive is not None and batch_archive.exists():
                _add_path_to_zip(zf, batch_archive, f"{suite_prefix}/go_batch_archives", added)
        zf.writestr(f"{combined_root.name}/bundle_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return bundle_path


def _cleanup_expanded_planning_results(*, combined_root: Path, suite_reports: Sequence[Mapping[str, Any]]) -> List[str]:
    deleted: List[str] = []
    targets: List[Path] = []
    for suite in suite_reports:
        result_root = str(suite.get("result_root") or "").strip()
        if result_root:
            targets.append(Path(result_root).resolve())
    targets.append(combined_root.resolve())
    seen: set[Path] = set()
    for path in sorted(targets, key=lambda item: len(item.parts), reverse=True):
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            shutil.rmtree(path)
            deleted.append(str(path))
        except Exception:
            continue
    return deleted


def load_benchmark_config(repo_root: Path, config_path: str | Path) -> Dict[str, Any]:
    _maybe_bootstrap_default_benchmark_assets(repo_root, config_path)
    resolved = _resolve_path(repo_root, config_path)
    if not resolved.exists():
        raise PipelineBenchmarkError(f"Benchmark config not found: {resolved}")
    data = _read_yaml(resolved)
    if not data:
        raise PipelineBenchmarkError(f"Benchmark config is empty or invalid: {resolved}")
    runner = _mapping(data.get("pipeline_benchmark"))
    suites = _list(runner.get("suites"))
    if not suites:
        raise PipelineBenchmarkError(f"Benchmark config does not define any suites: {resolved}")
    runner["config_path"] = str(resolved)
    return runner


def _suite_history_seconds(results_root: Path, suite_key: str) -> List[float]:
    history: List[float] = []
    for report_path in sorted(results_root.glob("*/pipeline_benchmark_report.json")):
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for suite in _list(report.get("suites")):
            suite_map = _mapping(suite)
            if str(suite_map.get("suite_key") or "") != suite_key:
                continue
            duration = suite_map.get("elapsed_seconds")
            try:
                seconds = float(duration)
            except Exception:
                continue
            if seconds > 0:
                history.append(seconds)
    return history


def _suite_eta_seconds(results_root: Path, suite_key: str) -> float:
    history = _suite_history_seconds(results_root, suite_key)
    if not history:
        return 0.0
    tail = history[-5:]
    return sum(tail) / max(len(tail), 1)


def _score_suite(
    *,
    repo_root: Path,
    suite_key: str,
    suite_cfg: Mapping[str, Any],
    suite_result_root: Path,
    batch_manifest: Mapping[str, Any],
) -> Dict[str, Any]:
    scorer = str(suite_cfg.get("scorer") or "").strip().lower()
    if not scorer:
        return {}
    batch_source = str(batch_manifest.get("archive_abspath") or "").strip()
    if not batch_source:
        return {}
    if scorer == "gold_large":
        try:
            from infra.pipeline.benchmark.file_selection_score import run_full_analysis  # type: ignore
        except Exception as exc:
            raise PipelineBenchmarkError(f"gold_large scorer is unavailable: {exc}") from exc
        benchmark_root = _resolve_path(repo_root, str(suite_cfg.get("benchmark_root") or "tests/benchmarks/pipeline/benchmark-gpt/gold_large"))
        return run_full_analysis(
            repo_root=repo_root,
            batch_source=batch_source,
            benchmark_root=str(benchmark_root),
            out_dir=suite_result_root / "scored",
            score_stage=str(suite_cfg.get("score_stage") or "auto"),
            allow_pis_fallback=bool(suite_cfg.get("allow_pis_fallback", False)),
            compare_batch=str(suite_cfg.get("compare_batch") or ""),
            skip_failure_analyses=bool(suite_cfg.get("skip_failure_analyses", False)),
        )
    try:
        from infra.pipeline.benchmark.promptset_score import run_all as run_promptset_score  # type: ignore
    except Exception as exc:
        raise PipelineBenchmarkError(f"promptset scorer is unavailable: {exc}") from exc
    benchmark_root = _resolve_path(repo_root, str(suite_cfg.get("benchmark_root") or "tests/benchmarks/pipeline"))
    return run_promptset_score(
        batch_source,
        benchmark_root,
        suite_result_root / "scored",
        str(suite_cfg.get("reference_dir") or ""),
    )


def _slugify(text: str, max_len: int = 48) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(text or "").strip()).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return (cleaned or "item")[:max_len].strip("-") or "item"


def _stage_mean_map(score_payload: Mapping[str, Any]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for stage, aggregate in _mapping(score_payload.get("stages")).items():
        mean = _mapping(aggregate).get("mean_score")
        try:
            out[str(stage)] = float(mean)
        except Exception:
            out[str(stage)] = None
    return out


def _extract_variant_summary(report: Mapping[str, Any]) -> Dict[str, Any]:
    suites_out: Dict[str, Any] = {}
    for suite in _list(report.get("suites")):
        suite_map = _mapping(suite)
        suite_key = str(suite_map.get("suite_key") or "").strip()
        if not suite_key:
            continue
        suites_out[suite_key] = {
            "status": str(suite_map.get("status") or ""),
            "elapsed_seconds": suite_map.get("elapsed_seconds"),
            "stages": _stage_mean_map(_mapping(suite_map.get("score"))),
        }
    return {
        "run_id": str(report.get("run_id") or ""),
        "status": str(report.get("status") or ""),
        "elapsed_seconds": report.get("elapsed_seconds"),
        "suites": suites_out,
    }


def _render_matrix_markdown(report: Mapping[str, Any]) -> str:
    variants = list(report.get("variants") or [])
    lines = [
        "# Pipeline Benchmark Variant Matrix",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- config: `{report.get('config_path')}`",
        f"- variant_count: `{len(variants)}`",
        f"- status: `{report.get('status')}`",
        "",
    ]
    baseline_key = str(report.get("baseline_variant") or "").strip()
    if baseline_key:
        lines.append(f"- baseline_variant: `{baseline_key}`")
        lines.append("")
    for variant in variants:
        variant_map = _mapping(variant)
        lines.extend([
            f"## {variant_map.get('variant_key')}",
            "",
            f"- status: `{variant_map.get('status')}`",
            f"- out_dir: `{variant_map.get('out_dir')}`",
            f"- elapsed_seconds: `{variant_map.get('elapsed_seconds')}`",
        ])
        description = str(variant_map.get("description") or "").strip()
        if description:
            lines.append(f"- description: {description}")
        overlays = list(variant_map.get("config_overlay_files") or [])
        if overlays:
            lines.append("- config_overlay_files:")
            for path in overlays:
                lines.append(f"  - `{path}`")
        env_map = _mapping(variant_map.get("env"))
        if env_map:
            lines.append("- env:")
            for key, value in sorted(env_map.items()):
                lines.append(f"  - `{key}={value}`")
        summary = _mapping(variant_map.get("summary"))
        if summary:
            lines.append("- suites:")
            for suite_key, suite_summary in sorted(_mapping(summary.get("suites")).items()):
                suite_map = _mapping(suite_summary)
                stage_bits = []
                for stage, mean in sorted(_mapping(suite_map.get("stages")).items()):
                    stage_bits.append(f"{stage}={mean}")
                bits = ", ".join(stage_bits) if stage_bits else "(no scored stages)"
                lines.append(f"  - `{suite_key}` status={suite_map.get('status')} {bits}")
        deltas = _mapping(variant_map.get("delta_vs_baseline"))
        if deltas:
            lines.append("- delta_vs_baseline:")
            for suite_key, suite_delta in sorted(deltas.items()):
                suite_map = _mapping(suite_delta)
                stage_bits = []
                for stage, delta in sorted(_mapping(suite_map.get("stages")).items()):
                    stage_bits.append(f"{stage}={delta:+.2f}")
                if stage_bits:
                    lines.append(f"  - `{suite_key}` " + ", ".join(stage_bits))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _print_suite_report_summary(report: Mapping[str, Any]) -> None:
    for suite in _list(report.get("suites")):
        suite_map = _mapping(suite)
        print(f"suite={suite_map.get('suite_key')}")
        print(f"  status={suite_map.get('status')}")
        if suite_map.get("batch_archive"):
            print(f"  batch_archive={suite_map['batch_archive']}")
        if suite_map.get("score"):
            score = _mapping(suite_map["score"])
            if score.get("runs"):
                for run in _list(score.get("runs")):
                    mode = str(run.get("mode") or "")
                    prefix = f"{mode} " if mode else ""
                    print(f"  score={prefix}{run.get('label')} mean={run.get('mean_score')} cases={run.get('scored_cases')}")
            for stage, aggregate in _mapping(score.get("stages")).items():
                aggregate_map = _mapping(aggregate)
                print(f"  {stage}=mean:{aggregate_map.get('mean_score')} cases:{aggregate_map.get('scored_cases')}")
        if suite_map.get("error"):
            print(f"  error={suite_map['error']}")


def _print_variant_report_summary(report: Mapping[str, Any]) -> None:
    for variant in _list(report.get("variants")):
        variant_map = _mapping(variant)
        print(f"variant={variant_map.get('variant_key')}")
        print(f"  status={variant_map.get('status')}")
        if variant_map.get("bundle_path"):
            print(f"  bundle_path={variant_map['bundle_path']}")
        summary = _mapping(variant_map.get("summary"))
        for suite_key, suite_summary in sorted(_mapping(summary.get("suites")).items()):
            suite_map = _mapping(suite_summary)
            print(f"  suite={suite_key} status={suite_map.get('status')} elapsed_seconds={suite_map.get('elapsed_seconds')}")
            for stage, mean in sorted(_mapping(suite_map.get("stages")).items()):
                print(f"    {stage}=mean:{mean}")


def _find_previous_batch_archive(benchmark_root: Path, current_run_id: str) -> str:
    candidates: List[Path] = []
    for archive in benchmark_root.glob("_results/*/go_batch_archives/*.zip"):
        if archive.parents[1].name == current_run_id:
            continue
        candidates.append(archive)
    if not candidates:
        return ""
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return str(candidates[0].resolve())


def _render_markdown(report: Mapping[str, Any]) -> str:
    suites = list(report.get("suites") or [])
    lines = [
        "# Pipeline Benchmark Run",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- config: `{report.get('config_path')}`",
        f"- suite_count: `{len(suites)}`",
        f"- status: `{report.get('status')}`",
        f"- bundle_path: `{report.get('bundle_path')}`" if report.get("bundle_path") else "- bundle_path: `(not generated)`",
        "",
    ]
    for suite in suites:
        lines.extend(
            [
                f"## {suite.get('suite_key')}",
                "",
                f"- status: `{suite.get('status')}`",
                f"- benchmark_root: `{suite.get('benchmark_root')}`",
                f"- batch_archive: `{suite.get('batch_archive')}`" if suite.get("batch_archive") else "- batch_archive: `(none)`",
                f"- previous_batch_archive: `{suite.get('previous_batch_archive')}`" if suite.get("previous_batch_archive") else "- previous_batch_archive: `(none)`",
                f"- result_root: `{suite.get('result_root')}`",
            ]
        )
        score = _mapping(suite.get("score"))
        if score:
            lines.append("- score_artifacts:")
            lines.append(f"  - out_dir: `{score.get('out_dir')}`")
            for run in _list(score.get("runs")):
                lines.append(f"  - {run.get('label')}: mean={run.get('mean_score')} cases={run.get('scored_cases')}")
            for stage, aggregate in _mapping(score.get("stages")).items():
                aggregate_map = _mapping(aggregate)
                lines.append(f"  - {stage}: mean={aggregate_map.get('mean_score')} cases={aggregate_map.get('scored_cases')}")
        error = str(suite.get("error") or "").strip()
        if error:
            lines.append(f"- error: `{error}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def list_suites(repo_root: Path, config_path: str | Path = DEFAULT_BENCHMARK_CONFIG) -> List[Dict[str, Any]]:
    runner_cfg = load_benchmark_config(repo_root, config_path)
    out: List[Dict[str, Any]] = []
    for raw in _list(runner_cfg.get("suites")):
        suite = _merged_suite_config(runner_cfg, _mapping(raw))
        key = _suite_key(suite)
        if not key:
            continue
        out.append(
            {
                "key": key,
                "enabled": bool(suite.get("enabled", True)),
                "scorer": str(suite.get("scorer") or ""),
                "item_kind": str(suite.get("item_kind") or "pis"),
                "items_dir": str(suite.get("items_dir") or ""),
            }
        )
    return out


def _run_pipeline_benchmark_once(
    *,
    repo_root: Path,
    runner_cfg: Mapping[str, Any],
    run_id: str,
    suite_keys: Optional[Iterable[str]] = None,
    variant_key: str = "",
    variant_description: str = "",
    variant_overlay_files: Optional[Sequence[str]] = None,
    variant_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    root = repo_root.resolve()
    selected = {str(item).strip() for item in list(suite_keys or []) if str(item).strip()}
    overall_started = time.time()

    active_suites: List[Dict[str, Any]] = []
    for raw in _list(runner_cfg.get("suites")):
        suite_cfg = _merged_suite_config(runner_cfg, _mapping(raw))
        suite_key = _suite_key(suite_cfg)
        if not suite_key:
            continue
        if selected and suite_key not in selected:
            continue
        if not bool(suite_cfg.get("enabled", True)):
            continue
        active_suites.append(suite_cfg)

    suite_reports: List[Dict[str, Any]] = []
    overall_ok = True
    interrupted = False
    combined_root = _resolve_path(root, str(runner_cfg.get("results_root") or "tests/benchmarks/pipeline/_results")) / run_id
    ensure_directory(combined_root)
    base_env = _mapping_str_str(_mapping(runner_cfg.get("env")))
    effective_env = dict(base_env)
    effective_env.update(dict(variant_env or {}))
    if variant_key:
        effective_env.setdefault("PIPELINE_BENCHMARK_VARIANT", variant_key)
    if variant_overlay_files:
        effective_env.setdefault("PIPELINE_CONFIG_OVERLAY_FILES", os.pathsep.join(str(path) for path in variant_overlay_files))

    for suite_index, suite_cfg in enumerate(active_suites, start=1):
        suite_key = _suite_key(suite_cfg)
        benchmark_root = _resolve_path(root, str(suite_cfg.get("benchmark_root") or "tests/benchmarks/pipeline"))
        suite_result_root = benchmark_root / "_results" / run_id
        ensure_directory(suite_result_root)
        suite_env = dict(effective_env)
        suite_env.update(_mapping_str_str(_mapping(suite_cfg.get("env"))))
        eta_seconds = _suite_eta_seconds(combined_root.parent, suite_key)
        suites_left_after = max(len(active_suites) - suite_index, 0)
        elapsed_before = time.time() - overall_started
        eta_label = _format_duration(eta_seconds) if eta_seconds > 0 else "unknown"
        variant_label = f"[{variant_key}] " if variant_key else ""
        _print_progress(
            f"{variant_label}starting suite {suite_index}/{len(active_suites)} `{suite_key}` "
            f"(elapsed {_format_duration(elapsed_before)}, suites left after this: {suites_left_after}, suite eta: {eta_label})"
        )

        suite_report: Dict[str, Any] = {
            "suite_key": suite_key,
            "benchmark_root": str(benchmark_root),
            "result_root": str(suite_result_root),
            "status": "ok",
        }
        suite_started = time.time()
        try:
            previous_batch_archive = _find_previous_batch_archive(benchmark_root, run_id)
            if previous_batch_archive:
                suite_report["previous_batch_archive"] = previous_batch_archive
            batch_manifest = run_pis_batch(
                repo_root=root,
                items_dir=str(suite_cfg.get("items_dir") or ""),
                pis_glob=str(suite_cfg.get("pis_glob") or ("*.md" if str(suite_cfg.get("item_kind") or "") == "prompt" else "*/pis.md")),
                item_kind=str(suite_cfg.get("item_kind") or "pis"),
                go_script=str(suite_cfg.get("go_script") or "infra/bin/go"),
                work_dir=str(suite_result_root.relative_to(root) / "go_batches"),
                archive_dir=str(suite_result_root.relative_to(root) / "go_batch_archives"),
                match=str(suite_cfg.get("match") or ""),
                limit=int(suite_cfg.get("limit") or 0),
                subset=str(suite_cfg.get("subset") or ""),
                first_uid=str(suite_cfg.get("first") or suite_cfg.get("first_uid") or ""),
                last_uid=str(suite_cfg.get("last") or suite_cfg.get("last_uid") or ""),
                provider=str(suite_cfg.get("provider") or ""),
                model=str(suite_cfg.get("model") or ""),
                preserve_semantic_cache=bool(suite_cfg.get("preserve_semantic_cache", False)),
                deterministic_only=bool(suite_cfg.get("deterministic_only", False)),
                pipeline_step_stop=int(suite_cfg.get("pipeline_step_stop") or 0),
                keep_generated=bool(suite_cfg.get("keep_generated", False)),
                batch_label=f"{variant_key}-{suite_key}" if variant_key else suite_key,
                extra_env=suite_env,
            )
            suite_report["batch_manifest"] = batch_manifest
            suite_report["batch_archive"] = str(batch_manifest.get("archive_abspath") or "")
            if bool(suite_cfg.get("compare_previous_run")) and not str(suite_cfg.get("compare_batch") or "").strip():
                if previous_batch_archive:
                    suite_cfg["compare_batch"] = previous_batch_archive
            suite_report["previous_batch_archive"] = str(suite_report.get("previous_batch_archive") or suite_cfg.get("compare_batch") or "")
            score_report = _score_suite(
                repo_root=root,
                suite_key=suite_key,
                suite_cfg=suite_cfg,
                suite_result_root=suite_result_root,
                batch_manifest=batch_manifest,
            )
            if score_report:
                suite_report["score"] = score_report
            if batch_manifest.get("status") != "ok":
                suite_report["status"] = str(batch_manifest.get("status") or "partial_failure")
                overall_ok = False
                if suite_report["status"] == "interrupted":
                    interrupted = True
        except (GoPisBatchError, PipelineBenchmarkError, FileNotFoundError, RuntimeError, ValueError) as exc:
            suite_report["status"] = "failed"
            suite_report["error"] = str(exc)
            overall_ok = False
        except KeyboardInterrupt:
            suite_report["status"] = "interrupted"
            suite_report["error"] = "Interrupted by user via Ctrl-C."
            overall_ok = False
            interrupted = True
        suite_elapsed = time.time() - suite_started
        suite_report["elapsed_seconds"] = round(suite_elapsed, 2)
        suite_report["elapsed_human"] = _format_duration(suite_elapsed)
        suite_reports.append(suite_report)
        _print_progress(
            f"{variant_label}finished suite {suite_index}/{len(active_suites)} `{suite_key}` "
            f"status={suite_report['status']} elapsed={suite_report['elapsed_human']}"
        )
        if interrupted:
            _print_progress(f"{variant_label}stopping remaining suites after interrupt")
            break

    report = {
        "run_id": run_id,
        "repo_root": str(root),
        "config_path": str(runner_cfg.get("config_path") or ""),
        "bundle_mode": str(runner_cfg.get("bundle_mode") or "slim"),
        "variant_key": variant_key,
        "variant_description": variant_description,
        "config_overlay_files": list(variant_overlay_files or []),
        "variant_env": dict(effective_env),
        "status": "interrupted" if interrupted else ("ok" if overall_ok else "partial_failure"),
        "elapsed_seconds": round(time.time() - overall_started, 2),
        "elapsed_human": _format_duration(time.time() - overall_started),
        "suites": suite_reports,
    }
    save_json_atomic(combined_root / "pipeline_benchmark_report.json", report)
    save_text_atomic(combined_root / "pipeline_benchmark_report.md", _render_markdown(report))
    bundle_path = _create_share_bundle(combined_root=combined_root, report=report, suite_reports=suite_reports)
    report["bundle_path"] = str(bundle_path)
    report["cleanup_deleted_paths"] = _cleanup_expanded_planning_results(combined_root=combined_root, suite_reports=suite_reports)
    report["out_dir"] = str(combined_root)
    save_json_atomic(combined_root / "pipeline_benchmark_report.json", report)
    save_text_atomic(combined_root / "pipeline_benchmark_report.md", _render_markdown(report))
    return report


def run_pipeline_benchmark(
    *,
    repo_root: str | Path = ".",
    config_path: str | Path = DEFAULT_BENCHMARK_CONFIG,
    suite_keys: Optional[Iterable[str]] = None,
    variant_keys: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    runner_cfg = load_benchmark_config(root, config_path)
    variant_selection = {str(item).strip() for item in list(variant_keys or []) if str(item).strip()}
    raw_variants = _list(runner_cfg.get("variants"))
    enabled_variants: List[Dict[str, Any]] = []
    for raw_variant in raw_variants:
        variant_cfg = _mapping(raw_variant)
        variant_key = str(variant_cfg.get("key") or variant_cfg.get("name") or "").strip()
        if not variant_key:
            continue
        if variant_selection and variant_key not in variant_selection:
            continue
        if not bool(variant_cfg.get("enabled", True)):
            continue
        enabled_variants.append(variant_cfg)

    if not enabled_variants:
        run_id = time.strftime("%Y.%m.%d-%H%M%S", time.gmtime())
        return _run_pipeline_benchmark_once(repo_root=root, runner_cfg=runner_cfg, run_id=run_id, suite_keys=suite_keys)

    matrix_run_id = time.strftime("%Y.%m.%d-%H%M%S", time.gmtime())
    matrix_root = _resolve_path(root, str(runner_cfg.get("results_root") or "tests/benchmarks/pipeline/_results")) / matrix_run_id
    ensure_directory(matrix_root)
    variant_reports: List[Dict[str, Any]] = []
    overall_ok = True
    interrupted = False
    for variant_cfg in enabled_variants:
        variant_key = str(variant_cfg.get("key") or variant_cfg.get("name") or "").strip()
        variant_slug = _slugify(variant_key)
        overlay_files = [str(_resolve_path(root, item)) for item in _list(variant_cfg.get("config_overlay_files")) if str(item).strip()]
        variant_env = {str(k): str(v) for k, v in _mapping(variant_cfg.get("env")).items() if str(k).strip()}
        suite_filter = list(suite_keys or [])
        if _list(variant_cfg.get("suite_keys")):
            suite_filter = [str(item) for item in _list(variant_cfg.get("suite_keys")) if str(item).strip()]
        variant_run_id = f"{matrix_run_id}__{variant_slug}"
        _print_progress(f"starting variant `{variant_key}` with run_id={variant_run_id}")
        report = _run_pipeline_benchmark_once(
            repo_root=root,
            runner_cfg=runner_cfg,
            run_id=variant_run_id,
            suite_keys=suite_filter,
            variant_key=variant_key,
            variant_description=str(variant_cfg.get("description") or ""),
            variant_overlay_files=overlay_files,
            variant_env=variant_env,
        )
        summary = _extract_variant_summary(report)
        variant_report = {
            "variant_key": variant_key,
            "description": str(variant_cfg.get("description") or ""),
            "status": str(report.get("status") or ""),
            "run_id": str(report.get("run_id") or ""),
            "out_dir": str(report.get("out_dir") or ""),
            "bundle_path": str(report.get("bundle_path") or ""),
            "elapsed_seconds": report.get("elapsed_seconds"),
            "config_overlay_files": overlay_files,
            "env": variant_env,
            "summary": summary,
        }
        variant_reports.append(variant_report)
        if report.get("status") != "ok":
            overall_ok = False
            if report.get("status") == "interrupted":
                interrupted = True
                break

    baseline_key = str(runner_cfg.get("baseline_variant") or (enabled_variants[0].get("key") if enabled_variants else "") or "").strip()
    baseline_summary = None
    for variant in variant_reports:
        if str(variant.get("variant_key") or "") == baseline_key:
            baseline_summary = _mapping(variant.get("summary"))
            break
    if baseline_summary:
        baseline_suites = _mapping(baseline_summary.get("suites"))
        for variant in variant_reports:
            current_suites = _mapping(_mapping(variant.get("summary")).get("suites"))
            delta_payload: Dict[str, Any] = {}
            for suite_key, suite_summary in current_suites.items():
                base_suite = _mapping(baseline_suites.get(suite_key))
                stage_deltas: Dict[str, float] = {}
                for stage, mean in _mapping(_mapping(suite_summary).get("stages")).items():
                    base_mean = _mapping(base_suite.get("stages")).get(stage)
                    try:
                        if mean is None or base_mean is None:
                            continue
                        stage_deltas[str(stage)] = round(float(mean) - float(base_mean), 4)
                    except Exception:
                        continue
                if stage_deltas:
                    delta_payload[suite_key] = {"stages": stage_deltas}
            if delta_payload:
                variant["delta_vs_baseline"] = delta_payload

    matrix_report = {
        "run_id": matrix_run_id,
        "repo_root": str(root),
        "config_path": str(runner_cfg.get("config_path") or ""),
        "bundle_mode": str(runner_cfg.get("bundle_mode") or "slim"),
        "status": "interrupted" if interrupted else ("ok" if overall_ok else "partial_failure"),
        "baseline_variant": baseline_key,
        "variants": variant_reports,
        "elapsed_seconds": round(sum(float(v.get("elapsed_seconds") or 0.0) for v in variant_reports), 2),
        "out_dir": str(matrix_root),
    }
    save_json_atomic(matrix_root / "pipeline_benchmark_report.json", matrix_report)
    save_text_atomic(matrix_root / "pipeline_benchmark_report.md", _render_matrix_markdown(matrix_report))
    matrix_report["bundle_path"] = str(_create_share_bundle(combined_root=matrix_root, report=matrix_report, suite_reports=[]))
    save_json_atomic(matrix_root / "pipeline_benchmark_report.json", matrix_report)
    save_text_atomic(matrix_root / "pipeline_benchmark_report.md", _render_matrix_markdown(matrix_report))
    return matrix_report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run configured pipeline benchmark suites and score them in one invocation.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--mode", choices=["planning", "execution"], default="planning")
    parser.add_argument("--config", default=DEFAULT_BENCHMARK_CONFIG)
    parser.add_argument("--suite", action="append", default=[], help="Only run the named suite. Repeatable.")
    parser.add_argument("--case", action="append", default=[], help="Only run the named execution benchmark case. Repeatable.")
    parser.add_argument("--stage", action="append", default=[], help="Execution benchmark stage selector. Repeatable; accepts csv or all.")
    parser.add_argument("--result-root", default="")
    parser.add_argument("--list-suites", action="store_true")
    parser.add_argument("--list-variants", action="store_true")
    parser.add_argument("--variant", action="append", default=[], help="Only run the named planning benchmark variant. Repeatable.")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--apply-confirmed-fixes", action="store_true", help="Apply confirmation-gated benchmark remediations in execution mode.")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if args.mode == "execution":
        ExecutionBenchmarkError, list_execution_cases, run_execution_benchmark = _load_execution_benchmark_api()
        if args.list_cases:
            payload = {"cases": list_execution_cases()}
            if args.json:
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                for case in payload["cases"]:
                    print(
                        f"{case['key']}	stages={','.join(str(v) for v in case['supported_stages'])}	"
                        f"target={case['terminal_stage']}:{case['terminal_verdict']}	{case['description']}"
                    )
            return 0
        try:
            report = run_execution_benchmark(
                repo_root=repo_root,
                result_root=args.result_root or None,
                case_keys=args.case,
                selected_stages=args.stage,
                progress=None if args.json else _print_progress,
                apply_confirmed_fixes=args.apply_confirmed_fixes,
            )
        except ExecutionBenchmarkError as exc:
            print(f"pipeline-benchmark: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"run_id={report['run_id']}")
            print(f"status={report['status']}")
            print(f"out_dir={report['out_dir']}")
            if report.get("bundle_path"):
                print(f"bundle_path={report['bundle_path']}")
            print(
                f"cases={report['executed_case_count']} matched={report['matched_case_count']} "
                f"mismatched={report['mismatched_case_count']} elapsed_seconds={report['elapsed_seconds']}"
            )
            if report.get("confirmation_required_count") or report.get("confirmation_applied_count") or report.get("auto_fix_count"):
                print(
                    f"confirmation_required={report.get('confirmation_required_count', 0)} "
                    f"confirmation_applied={report.get('confirmation_applied_count', 0)} "
                    f"auto_fixes={report.get('auto_fix_count', 0)}"
                )
            for stage in report["stage_summary"]:
                print(
                    f"stage={stage['stage_number']} executions={stage['execution_count']} "
                    f"target_matches={stage['target_expectation_matches']}/{stage['target_case_count']} "
                    f"avg_seconds={stage['average_elapsed_seconds']}"
                )
        return 0 if report.get("status") == "ok" else 1

    if args.list_suites:
        payload = {"config": str(_resolve_path(repo_root, args.config)), "suites": list_suites(repo_root, args.config)}
        if args.json:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            for suite in payload["suites"]:
                print(
                    f"{suite['key']}\tenabled={suite['enabled']}\tscorer={suite['scorer'] or 'auto'}\titem_kind={suite['item_kind']}\titems_dir={suite['items_dir']}"
                )
        return 0

    try:
        report = run_pipeline_benchmark(repo_root=repo_root, config_path=args.config, suite_keys=args.suite, variant_keys=args.variant)
    except PipelineBenchmarkError as exc:
        print(f"pipeline-benchmark: {exc}")
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"run_id={report['run_id']}")
        print(f"status={report['status']}")
        print(f"out_dir={report['out_dir']}")
        if report.get("bundle_path"):
            print(f"bundle_path={report['bundle_path']}")
        if report.get("cleanup_deleted_paths"):
            print(f"cleanup_deleted_paths={len(list(report.get('cleanup_deleted_paths') or []))}")
        if _list(report.get("variants")):
            _print_variant_report_summary(report)
        else:
            _print_suite_report_summary(report)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
