from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple


SCENARIO_FILE = Path(__file__).with_name("scenarios.json")


@dataclass(frozen=True)
class ScenarioSpec:
    key: str
    description: str
    required_dimensions: Tuple[str, ...]
    required_artifacts: Mapping[str, Tuple[str, ...]]
    complexity_budget: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeMetrics:
    file_count: int
    nonempty_line_count: int
    branch_count: int
    function_count: int


@dataclass(frozen=True)
class SchedulerSnapshot:
    schema_version: str
    snapshot_label: str
    scheduler_kind: str
    adapter_kind: str
    captured_at_utc: str
    scenario_key: str
    jobs: Tuple[Mapping[str, Any], ...] = ()
    receipts: Tuple[Mapping[str, Any], ...] = ()
    compiled_graphs: Tuple[Mapping[str, Any], ...] = ()
    continuity_records: Tuple[Mapping[str, Any], ...] = ()
    closeout_records: Tuple[Mapping[str, Any], ...] = ()
    surface_projections: Mapping[str, Tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    family_records: Tuple[Mapping[str, Any], ...] = ()
    code_metrics: CodeMetrics | None = None
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["jobs"] = list(self.jobs)
        payload["receipts"] = list(self.receipts)
        payload["compiled_graphs"] = list(self.compiled_graphs)
        payload["continuity_records"] = list(self.continuity_records)
        payload["closeout_records"] = list(self.closeout_records)
        payload["surface_projections"] = {key: list(value) for key, value in self.surface_projections.items()}
        payload["family_records"] = list(self.family_records)
        payload["notes"] = list(self.notes)
        return payload


@dataclass(frozen=True)
class BenchmarkDimensionResult:
    dimension: str
    verdict: str
    summary: str
    details: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkRunResult:
    status: str
    scenario_key: str
    baseline_label: str
    candidate_label: str
    dimensions: Tuple[BenchmarkDimensionResult, ...]
    baseline_snapshot: Mapping[str, Any]
    candidate_snapshot: Mapping[str, Any]
    report_dir: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "scenario_key": self.scenario_key,
            "baseline_label": self.baseline_label,
            "candidate_label": self.candidate_label,
            "dimensions": [asdict(item) for item in self.dimensions],
            "baseline_snapshot": dict(self.baseline_snapshot),
            "candidate_snapshot": dict(self.candidate_snapshot),
            "report_dir": self.report_dir,
        }


def load_scenarios(path: Path | None = None) -> Dict[str, ScenarioSpec]:
    source = Path(path) if path is not None else SCENARIO_FILE
    payload = json.loads(source.read_text(encoding="utf-8"))
    scenarios: Dict[str, ScenarioSpec] = {}
    for row in payload.get("scenarios") or []:
        spec = ScenarioSpec(
            key=str(row.get("key") or "").strip(),
            description=str(row.get("description") or "").strip(),
            required_dimensions=tuple(str(item).strip() for item in row.get("required_dimensions") or [] if str(item).strip()),
            required_artifacts={
                str(key).strip(): tuple(str(item).strip() for item in value or [] if str(item).strip())
                for key, value in (row.get("required_artifacts") or {}).items()
                if str(key).strip()
            },
            complexity_budget={str(key).strip(): float(value) for key, value in (row.get("complexity_budget") or {}).items()},
        )
        if spec.key:
            scenarios[spec.key] = spec
    return scenarios
