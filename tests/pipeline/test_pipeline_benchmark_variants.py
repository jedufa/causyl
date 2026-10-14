from __future__ import annotations

import json
import zipfile
from pathlib import Path

from infra.pipeline.core import load_pipeline_config
from infra.pipeline.pipeline_benchmark import _create_share_bundle, load_benchmark_config, run_pipeline_benchmark


def test_load_pipeline_config_applies_overlay_files_from_env(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        """
retrieval_policy:
  stage4_structural_hint_limit: 99
""",
        encoding="utf-8",
    )

    config = load_pipeline_config(
        repo_root=repo_root,
        env={"PIPELINE_CONFIG_OVERLAY_FILES": str(overlay)},
    )

    assert config["retrieval_policy"]["stage4_structural_hint_limit"] == 99
    assert config["_derived"]["config_overlay_files"] == [str(overlay.resolve())]


def test_run_pipeline_benchmark_executes_named_variants_with_overlay_env(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config_path = repo_root / "benchmark.yaml"
    overlay_path = repo_root / "variant_overlay.yaml"
    overlay_path.write_text("retrieval_policy:\n  owner_promotion_strength: 1.5\n", encoding="utf-8")
    benchmark_root = repo_root / "benchmarks" / "pipeline"
    benchmark_root.mkdir(parents=True)
    config_path.write_text(
        """
pipeline_benchmark:
  results_root: benchmark_runs
  baseline_variant: control
  suites:
    - key: toy
      enabled: true
      benchmark_root: tests/benchmarks/pipeline
      items_dir: tests/fixtures
      scorer: gold_large
  variants:
    - key: control
      description: baseline
    - key: stage4_local_expansion
      description: expanded
      config_overlay_files:
        - variant_overlay.yaml
      env:
        PIPELINE_EXTRA_FLAG: enabled
""",
        encoding="utf-8",
    )

    calls = []

    def _fake_run_pis_batch(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "archive_abspath": str(repo_root / "dummy.zip")}

    def _fake_score_suite(**kwargs):
        variant = (kwargs["batch_manifest"] or {}).get("_variant_key") or kwargs["suite_cfg"].get("_variant_key") or ""
        mean = 10.0 if variant == "control" else 12.5
        return {"stages": {"stage4": {"mean_score": mean, "scored_cases": 1}}}

    monkeypatch.setattr("infra.pipeline.pipeline_benchmark.run_pis_batch", _fake_run_pis_batch)
    monkeypatch.setattr("infra.pipeline.pipeline_benchmark._score_suite", _fake_score_suite)

    # enrich manifests after fake run to let scorer infer variant by patched suite cfg
    from infra.pipeline import pipeline_benchmark as pb

    original_run_once = pb._run_pipeline_benchmark_once

    def _wrapped_run_once(**kwargs):
        report = original_run_once(**kwargs)
        return report

    monkeypatch.setattr(pb, "_run_pipeline_benchmark_once", _wrapped_run_once)

    # easier: patch scorer input through fake run_pis_batch side effect using call order
    def _fake_run_pis_batch_variant(**kwargs):
        manifest = {"status": "ok", "archive_abspath": str(repo_root / "dummy.zip"), "_variant_key": kwargs.get("extra_env", {}).get("PIPELINE_BENCHMARK_VARIANT", "")}
        calls.append(kwargs)
        return manifest

    monkeypatch.setattr(pb, "run_pis_batch", _fake_run_pis_batch_variant)

    report = run_pipeline_benchmark(repo_root=repo_root, config_path=config_path)

    assert report["baseline_variant"] == "control"
    assert len(report["variants"]) == 2
    assert calls[0]["extra_env"]["PIPELINE_BENCHMARK_VARIANT"] == "control"
    assert calls[1]["extra_env"]["PIPELINE_BENCHMARK_VARIANT"] == "stage4_local_expansion"
    assert "PIPELINE_CONFIG_OVERLAY_FILES" not in calls[0]["extra_env"]
    assert calls[1]["extra_env"]["PIPELINE_CONFIG_OVERLAY_FILES"] == str(overlay_path.resolve())
    assert calls[1]["extra_env"]["PIPELINE_EXTRA_FLAG"] == "enabled"
    variant_map = {item["variant_key"]: item for item in report["variants"]}
    assert variant_map["stage4_local_expansion"]["delta_vs_baseline"]["toy"]["stages"]["stage4"] == 2.5
    assert Path(report["out_dir"]).joinpath("pipeline_benchmark_report.json").exists()
    payload = json.loads(Path(report["out_dir"]).joinpath("pipeline_benchmark_report.json").read_text(encoding="utf-8"))
    assert payload["variants"][1]["variant_key"] == "stage4_local_expansion"


def test_load_benchmark_config_bootstraps_default_assets_from_sibling_repo(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    repo_root = parent / "repo"
    sibling_root = parent / "causyl"
    repo_root.mkdir(parents=True)
    sibling_root.mkdir(parents=True)

    source_benchmark_dir = sibling_root / "tests" / "benchmarks" / "pipeline"
    source_benchmark_dir.mkdir(parents=True)
    (source_benchmark_dir / "pipeline_benchmark.yaml").write_text(
        """
pipeline_benchmark:
  results_root: tests/benchmarks/pipeline/_results
  suites:
    - key: toy
      enabled: true
      benchmark_root: tests/benchmarks/pipeline/benchmark-gpt/gold_large
      items_dir: tests/benchmarks/pipeline/benchmark-gpt/gold_large/gold_items_large
""",
        encoding="utf-8",
    )
    source_assets = source_benchmark_dir / "benchmark-gpt" / "gold_large" / "gold_items_large"
    source_assets.mkdir(parents=True)

    runner = load_benchmark_config(repo_root, "tests/benchmarks/pipeline/pipeline_benchmark.yaml")

    target_config = repo_root / "tests" / "benchmarks" / "pipeline" / "pipeline_benchmark.yaml"
    target_assets = repo_root / "tests" / "benchmarks" / "pipeline" / "benchmark-gpt"
    assert runner["config_path"] == str(target_config.resolve())
    assert target_config.is_symlink()
    assert target_assets.is_symlink()
    assert target_config.resolve() == (source_benchmark_dir / "pipeline_benchmark.yaml").resolve()
    assert target_assets.resolve() == (source_benchmark_dir / "benchmark-gpt").resolve()


def test_load_benchmark_config_bootstraps_assets_when_default_config_is_tracked(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    repo_root = parent / "repo"
    sibling_root = parent / "causyl"
    repo_root.mkdir(parents=True)
    sibling_root.mkdir(parents=True)

    target_benchmark_dir = repo_root / "tests" / "benchmarks" / "pipeline"
    target_benchmark_dir.mkdir(parents=True)
    (target_benchmark_dir / "pipeline_benchmark.yaml").write_text(
        """
pipeline_benchmark:
  results_root: tests/benchmarks/pipeline/_results
  suites:
    - key: toy
      enabled: true
      benchmark_root: tests/benchmarks/pipeline/benchmark-gpt/gold_large
      items_dir: tests/benchmarks/pipeline/benchmark-gpt/gold_large/gold_items_large
""",
        encoding="utf-8",
    )

    source_benchmark_dir = sibling_root / "tests" / "benchmarks" / "pipeline"
    source_benchmark_dir.mkdir(parents=True)
    source_assets = source_benchmark_dir / "benchmark-gpt" / "gold_large" / "gold_items_large"
    source_assets.mkdir(parents=True)

    runner = load_benchmark_config(repo_root, "tests/benchmarks/pipeline/pipeline_benchmark.yaml")

    target_assets = repo_root / "tests" / "benchmarks" / "pipeline" / "benchmark-gpt"
    assert runner["config_path"] == str((target_benchmark_dir / "pipeline_benchmark.yaml").resolve())
    assert not (target_benchmark_dir / "pipeline_benchmark.yaml").is_symlink()
    assert target_assets.is_symlink()
    assert target_assets.resolve() == (source_benchmark_dir / "benchmark-gpt").resolve()


def test_run_pipeline_benchmark_passes_default_and_suite_env(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config_path = repo_root / "benchmark.yaml"
    config_path.write_text(
        """
pipeline_benchmark:
  results_root: benchmark_runs
  env:
    PIPELINE_STAGE8_PATCH_RESPONSE_FILE: tests/fixtures/stage8_responses/success_unified_diff.txt
  suites:
    - key: toy
      enabled: true
      benchmark_root: tests/benchmarks/pipeline
      items_dir: tests/fixtures
      scorer: gold_large
      env:
        PIPELINE_EXTRA_FLAG: enabled
""",
        encoding="utf-8",
    )

    calls = []

    def _fake_run_pis_batch(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "archive_abspath": str(repo_root / "dummy.zip")}

    def _fake_score_suite(**kwargs):
        return {"stages": {"stage4": {"mean_score": 10.0, "scored_cases": 1}}}

    monkeypatch.setattr("infra.pipeline.pipeline_benchmark.run_pis_batch", _fake_run_pis_batch)
    monkeypatch.setattr("infra.pipeline.pipeline_benchmark._score_suite", _fake_score_suite)

    report = run_pipeline_benchmark(repo_root=repo_root, config_path=config_path)

    assert report["status"] == "ok"
    assert calls[0]["extra_env"]["PIPELINE_STAGE8_PATCH_RESPONSE_FILE"] == "tests/fixtures/stage8_responses/success_unified_diff.txt"
    assert calls[0]["extra_env"]["PIPELINE_EXTRA_FLAG"] == "enabled"


def test_create_share_bundle_defaults_to_slim_and_full_mode_keeps_archives(tmp_path: Path) -> None:
    combined_root = tmp_path / "results" / "run-123"
    combined_root.mkdir(parents=True)
    (combined_root / "pipeline_benchmark_report.json").write_text("{}", encoding="utf-8")
    (combined_root / "pipeline_benchmark_report.md").write_text("# report\n", encoding="utf-8")

    suite_root = tmp_path / "suite_result"
    scored_root = suite_root / "scored"
    archive_root = suite_root / "go_batch_archives"
    scored_root.mkdir(parents=True)
    archive_root.mkdir(parents=True)
    (scored_root / "summary.json").write_text('{"score": 1}', encoding="utf-8")
    (archive_root / "batch.zip").write_text("huge", encoding="utf-8")

    batch_archive = archive_root / "batch.zip"
    suite_reports = [{"suite_key": "toy", "result_root": str(suite_root), "batch_archive": str(batch_archive)}]

    slim_bundle = _create_share_bundle(
        combined_root=combined_root,
        report={"run_id": "run-123", "status": "ok", "bundle_mode": "slim"},
        suite_reports=suite_reports,
    )
    with zipfile.ZipFile(slim_bundle) as zf:
        names = set(zf.namelist())
    assert "run-123/toy/scored/summary.json" in names
    assert "run-123/toy/go_batch_archives/batch.zip" in names

    full_bundle = _create_share_bundle(
        combined_root=combined_root,
        report={"run_id": "run-123", "status": "ok", "bundle_mode": "full"},
        suite_reports=suite_reports,
    )
    with zipfile.ZipFile(full_bundle) as zf:
        names = set(zf.namelist())
    assert "run-123/toy/scored/summary.json" in names
    assert "run-123/toy/go_batch_archives/batch.zip" in names
