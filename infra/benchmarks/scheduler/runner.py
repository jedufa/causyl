from __future__ import annotations

from pathlib import Path

from .adapters import SchedulerBenchmarkAdapter
from .compare import compare_snapshots, summarize_status
from .contracts import BenchmarkRunResult, ScenarioSpec
from .reporting import write_benchmark_report


def run_scheduler_benchmark(
    *,
    baseline_adapter: SchedulerBenchmarkAdapter,
    candidate_adapter: SchedulerBenchmarkAdapter,
    scenario: ScenarioSpec,
    report_dir: Path,
) -> BenchmarkRunResult:
    baseline = baseline_adapter.capture(scenario=scenario)
    candidate = candidate_adapter.capture(scenario=scenario)
    dimensions = compare_snapshots(baseline=baseline, candidate=candidate, scenario=scenario)
    result = BenchmarkRunResult(
        status=summarize_status(dimensions),
        scenario_key=scenario.key,
        baseline_label=baseline.snapshot_label,
        candidate_label=candidate.snapshot_label,
        dimensions=dimensions,
        baseline_snapshot=baseline.to_dict(),
        candidate_snapshot=candidate.to_dict(),
        report_dir=str(report_dir.resolve()),
    )
    write_benchmark_report(result, report_dir)
    return result
