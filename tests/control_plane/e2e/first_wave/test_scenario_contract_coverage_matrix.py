from __future__ import annotations

from pathlib import Path

from .helpers.contracts import expected_readme_coverage_lines


def _readme_coverage_lines() -> tuple[str, ...]:
    readme = (Path(__file__).resolve().parent / "README.md").read_text(encoding="utf-8").splitlines()
    start = readme.index("## Scenario To Intent Coverage") + 1
    end = next(index for index in range(start, len(readme)) if readme[index].startswith("## "))
    return tuple(line for line in readme[start:end] if line.strip())


def test_readme_scenario_to_intent_coverage_matrix_matches_contracts() -> None:
    assert _readme_coverage_lines() == expected_readme_coverage_lines()
