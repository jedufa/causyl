from __future__ import annotations

import json
from pathlib import Path

from tools.bin import watchdog_alertability_validation as validation


def test_watchdog_alertability_validation_writes_harsh_alertability_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"

    matrix = validation.run_validation(out_dir=out_dir)

    assert matrix["scenario_count"] == 4
    assert "watchdog_web_projection_allowed_false_calm_from_open_incident_index" in matrix["bugs_found_and_fixed"]

    controls = json.loads((out_dir / "false_calm_incidents.json").read_text(encoding="utf-8"))
    assert len(controls["controls"]) == 2
    assert any(row["assertion"] == "missing_watchdog_report_with_open_incident_is_not_treated_as_quiet" for row in controls["controls"])
