from __future__ import annotations

import json
from pathlib import Path

from tools.bin import cross_family_interference_validation as validation


def test_cross_family_interference_validation_writes_family_scoped_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"

    matrix = validation.run_validation(out_dir=out_dir)

    assert matrix["scenario_count"] == 5
    assert "watchdog_codex_dedupe_not_family_scoped" in matrix["bugs_found_and_fixed"]

    matrix_path = out_dir / "cross_family_interference_matrix.json"
    controls_path = out_dir / "cross_family_negative_controls.json"
    trace_path = out_dir / "cross_family_truth_trace.jsonl"

    assert matrix_path.exists()
    assert controls_path.exists()
    assert trace_path.exists()

    controls = json.loads(controls_path.read_text(encoding="utf-8"))
    assert len(controls["controls"]) == 2
    assert any(row["assertion"] == "family_b_does_not_dedupe_against_family_a_receipt" for row in controls["controls"])
