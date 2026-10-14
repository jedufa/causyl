from __future__ import annotations

import json
from pathlib import Path

from .contracts import BenchmarkRunResult


def write_benchmark_report(result: BenchmarkRunResult, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "scheduler_benchmark_report.json"
    md_path = report_dir / "scheduler_benchmark_report.md"
    json_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# Scheduler Benchmark Report",
        "",
        f"- status: `{result.status}`",
        f"- scenario: `{result.scenario_key}`",
        f"- baseline: `{result.baseline_label}`",
        f"- candidate: `{result.candidate_label}`",
        "",
        "## Dimensions",
        "",
    ]
    for item in result.dimensions:
        lines.append(f"- `{item.dimension}`: `{item.verdict}`")
        lines.append(f"  - {item.summary}")
        for detail in item.details:
            lines.append(f"  - {detail}")
    lines.extend(
        [
            "",
            "## Snapshot Labels",
            "",
            f"- baseline snapshot: `{result.baseline_snapshot.get('snapshot_label', '')}`",
            f"- candidate snapshot: `{result.candidate_snapshot.get('snapshot_label', '')}`",
            "",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
