"""Minimal v2 scheduler runtime for one intent-family workflow."""

from .runtime import (
    collect_current_runtime_records,
    compile_scheduler_plan,
    evaluate_scheduler_plan,
    run_shadow_tick,
)
from .ownership import (
    assert_canonical_write_allowed,
    assert_v2_can_evaluate_family,
    load_ownership,
    ownership_receipt,
)
from .cutover import evaluate_cutover_gates
from .surfaces import build_internal_surface_bundle, load_production_wiring_contract
from .fanout import (
    compile_fanout_authoring_plan,
    materialize_fanout_for_intent,
    write_fanout_authoring_plan_artifact,
)


def load_canary_target(*args, **kwargs):
    from .canary import load_canary_target as _load_canary_target

    return _load_canary_target(*args, **kwargs)


def run_real_pis_shadow_canary(*args, **kwargs):
    from .canary import run_real_pis_shadow_canary as _run_real_pis_shadow_canary

    return _run_real_pis_shadow_canary(*args, **kwargs)

__all__ = [
    "collect_current_runtime_records",
    "compile_scheduler_plan",
    "evaluate_scheduler_plan",
    "evaluate_cutover_gates",
    "assert_canonical_write_allowed",
    "assert_v2_can_evaluate_family",
    "build_internal_surface_bundle",
    "compile_fanout_authoring_plan",
    "load_ownership",
    "load_canary_target",
    "load_production_wiring_contract",
    "materialize_fanout_for_intent",
    "ownership_receipt",
    "run_real_pis_shadow_canary",
    "run_shadow_tick",
    "write_fanout_authoring_plan_artifact",
]
