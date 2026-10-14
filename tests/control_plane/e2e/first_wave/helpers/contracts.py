from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path


REQUIRED_ASSERTION_LADDER = (
    "entry truth",
    "execution truth",
    "policy truth",
    "restart truth",
    "projection truth",
    "closure truth",
    "artifact truth",
    "repair truth",
)


@dataclass(frozen=True)
class ScenarioContract:
    scenario_id: str
    operator_job: str
    capability: str
    durable_truth: tuple[str, ...]
    manual_repair_eliminated: str
    primary_intent_sheets: tuple[str, ...]
    allowed_shortcuts: tuple[str, ...]
    forbidden_shortcuts: tuple[str, ...]
    supporting_intent_sheets: tuple[str, ...] = ()
    required_assertions: tuple[str, ...] = field(default=REQUIRED_ASSERTION_LADDER)


def assert_scenario_contract(contract: ScenarioContract) -> None:
    assert contract.scenario_id.strip()
    assert contract.operator_job.strip()
    assert contract.capability.strip()
    assert contract.manual_repair_eliminated.strip()
    assert contract.durable_truth
    assert contract.primary_intent_sheets
    assert contract.allowed_shortcuts
    assert contract.forbidden_shortcuts
    assert contract.required_assertions == REQUIRED_ASSERTION_LADDER
    assert all(item.startswith("IS-") for item in contract.primary_intent_sheets)
    assert all(item.startswith("IS-") for item in contract.supporting_intent_sheets)
    assert any("fixture seeding" in item for item in contract.allowed_shortcuts)
    assert any("direct state mutation after scenario start" in item for item in contract.forbidden_shortcuts)


def _first_wave_root() -> Path:
    return Path(__file__).resolve().parents[1]


def scenario_module_names() -> tuple[str, ...]:
    root = _first_wave_root()
    return tuple(
        sorted(
            path.stem
            for path in root.glob("test_*.py")
            if path.stem != "test_scenario_contract_coverage_matrix"
        )
    )


def load_first_wave_scenario_contracts() -> tuple[tuple[str, ScenarioContract], ...]:
    rows: list[tuple[str, ScenarioContract]] = []
    for module_name in scenario_module_names():
        module = importlib.import_module(f"tests.control_plane.e2e.first_wave.{module_name}")
        contract = getattr(module, "CONTRACT", None)
        if isinstance(contract, ScenarioContract):
            rows.append((module_name, contract))
    return tuple(rows)


def render_intent_coverage_line(module_name: str, contract: ScenarioContract) -> str:
    primary = ", ".join(f"`{item}`" for item in contract.primary_intent_sheets)
    line = f"- `{module_name}.py`: direct {primary}"
    if contract.supporting_intent_sheets:
        supporting = ", ".join(f"`{item}`" for item in contract.supporting_intent_sheets)
        line += f"; supporting {supporting}"
    return line


def expected_readme_coverage_lines() -> tuple[str, ...]:
    return tuple(
        render_intent_coverage_line(module_name, contract)
        for module_name, contract in load_first_wave_scenario_contracts()
    )
