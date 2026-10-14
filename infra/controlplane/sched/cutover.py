from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .ownership import load_ownership
from .surfaces import build_internal_surface_bundle, load_production_wiring_contract


CUTOVER_GATES_SCHEMA_VERSION = "sched_v2_cutover_gates_v1"


def _repo_root(repo_root: Path | None = None) -> Path:
    return Path(repo_root or ".").resolve()


def _read_json(path: Path) -> Mapping[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dimension_status(report: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in list(report.get("dimensions") or []):
        if isinstance(row, Mapping):
            out[str(row.get("dimension") or "")] = str(row.get("verdict") or "")
    return out


def _gate(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def evaluate_cutover_gates(
    *,
    repo_root: Path | None = None,
    benchmark_report_path: Path | None = None,
    parent_uid: str = "",
    target_mode: str = "v2_primary",
    allow_primary: bool = False,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    report_path = (
        Path(benchmark_report_path).resolve()
        if benchmark_report_path is not None
        else (root / "governance/evidence/benchmarks/scheduler/_results/production_stress_shadow/scheduler_benchmark_report.json").resolve()
    )
    report: Mapping[str, Any] = {}
    report_error = ""
    if report_path.exists():
        try:
            report = _read_json(report_path)
        except Exception as exc:
            report_error = f"{type(exc).__name__}: {exc}"
    else:
        report_error = f"missing_report:{report_path}"

    dimensions = _dimension_status(report)
    wiring = load_production_wiring_contract(repo_root=root)
    surface_bundle = build_internal_surface_bundle(
        repo_root=root,
        parent_uid=parent_uid,
        mode="shadow",
    )
    ownership = load_ownership(mode=target_mode)
    required_dimensions = {
        "operator_truth",
        "dependency_admission",
        "stress_volume",
        "destructive_conditions",
        "ownership_isolation",
        "internal_wiring",
        "complexity",
    }
    missing_or_bad = sorted(name for name in required_dimensions if dimensions.get(name) != "pass")
    gates = [
        _gate("benchmark_report_passed", str(report.get("status") or "") == "pass", str(report_error or report.get("status") or "")),
        _gate("required_dimensions_passed", not missing_or_bad, ",".join(missing_or_bad)),
        _gate("no_new_public_port", wiring["internal_wiring"].get("public_ports") == "no new public port for v2", wiring["internal_wiring"].get("public_ports", "")),
        _gate("surface_projection_equivalence", bool(surface_bundle.get("projection_equivalent")), str(surface_bundle.get("status") or "")),
        _gate(
            "watchdog_no_direct_truth_mutation",
            "must not directly mutate" in str(wiring["internal_wiring"].get("watchdog") or ""),
            str(wiring["internal_wiring"].get("watchdog") or ""),
        ),
        _gate(
            "closeout_only_promotion",
            "closeout" in str(wiring.get("production_gates") or "").lower(),
            "production_gates contains closeout requirement",
        ),
        _gate(
            "primary_requires_explicit_allow",
            str(ownership.mode) != "v2_primary" or bool(allow_primary),
            f"target_mode={ownership.mode} allow_primary={allow_primary}",
        ),
    ]
    ready = all(bool(row["ok"]) for row in gates)
    return {
        "schema_version": CUTOVER_GATES_SCHEMA_VERSION,
        "ready": ready,
        "target_mode": ownership.mode,
        "canonical_owner_after_cutover": ownership.canonical_owner,
        "benchmark_report_path": str(report_path.relative_to(root)) if str(report_path).startswith(str(root)) else str(report_path),
        "gates": gates,
        "surface_bundle_status": str(surface_bundle.get("status") or ""),
        "public_port_policy": wiring["internal_wiring"].get("public_ports"),
        "next_action": "enable_v2_primary" if ready else "remain_legacy_or_shadow",
    }
