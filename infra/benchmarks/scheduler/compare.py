from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence

from .contracts import BenchmarkDimensionResult, CodeMetrics, ScenarioSpec, SchedulerSnapshot


def _by_job_id(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    return {str(row.get("job_id") or ""): row for row in rows if str(row.get("job_id") or "")}


def _pending(dimension: str, summary: str) -> BenchmarkDimensionResult:
    return BenchmarkDimensionResult(dimension=dimension, verdict="pending_interface", summary=summary)


def _pass(dimension: str, summary: str, *details: str) -> BenchmarkDimensionResult:
    return BenchmarkDimensionResult(dimension=dimension, verdict="pass", summary=summary, details=tuple(details))


def _fail(dimension: str, summary: str, *details: str) -> BenchmarkDimensionResult:
    return BenchmarkDimensionResult(dimension=dimension, verdict="fail", summary=summary, details=tuple(details))


def _compare_operator_truth(baseline: SchedulerSnapshot, candidate: SchedulerSnapshot) -> BenchmarkDimensionResult:
    if not baseline.jobs or not candidate.jobs:
        return _pending("operator_truth", "missing job snapshots for one or both schedulers")
    base_jobs = _by_job_id(baseline.jobs)
    cand_jobs = _by_job_id(candidate.jobs)
    missing = sorted(job_id for job_id in base_jobs if job_id not in cand_jobs)
    mismatches = []
    for job_id, base_row in base_jobs.items():
        cand_row = cand_jobs.get(job_id)
        if not cand_row:
            continue
        for field in ("projected_status", "projection_reason", "canonical_state", "due_reason"):
            if str(base_row.get(field) or "") != str(cand_row.get(field) or ""):
                mismatches.append(
                    f"{job_id} {field}: baseline={base_row.get(field)!r} candidate={cand_row.get(field)!r}"
                )
    if missing or mismatches:
        details = tuple((["missing jobs: " + ", ".join(missing)] if missing else []) + mismatches[:12])
        return _fail("operator_truth", "candidate diverged from baseline operator truth", *details)
    return _pass("operator_truth", f"matched operator truth for {len(base_jobs)} jobs")


def _compare_dependency_admission(baseline: SchedulerSnapshot, candidate: SchedulerSnapshot) -> BenchmarkDimensionResult:
    if not baseline.jobs or not candidate.jobs:
        return _pending("dependency_admission", "missing job snapshots for dependency comparison")
    base_jobs = _by_job_id(baseline.jobs)
    cand_jobs = _by_job_id(candidate.jobs)
    mismatches = []
    for job_id, base_row in base_jobs.items():
        cand_row = cand_jobs.get(job_id)
        if not cand_row:
            continue
        if list(base_row.get("depends_on") or []) != list(cand_row.get("depends_on") or []):
            mismatches.append(f"{job_id} depends_on drifted")
        base_blocked = str(base_row.get("projected_status") or "") == "blocked"
        cand_blocked = str(cand_row.get("projected_status") or "") == "blocked"
        if base_blocked != cand_blocked:
            mismatches.append(f"{job_id} blocked parity drifted")
    if mismatches:
        return _fail("dependency_admission", "dependency or blocked-state parity drifted", *mismatches[:12])
    return _pass("dependency_admission", f"dependency and blocked-state parity matched for {len(base_jobs)} jobs")


def _compare_terminal_retirement(baseline: SchedulerSnapshot, candidate: SchedulerSnapshot) -> BenchmarkDimensionResult:
    if not baseline.receipts or not candidate.receipts:
        return _pending("terminal_retirement", "missing receipts for one or both schedulers")
    def latest_status(rows: Sequence[Mapping[str, Any]]) -> Dict[str, tuple[str, bool]]:
        out: Dict[str, tuple[str, bool]] = {}
        for row in rows:
            job_id = str(row.get("job_id") or "")
            if not job_id or job_id in out:
                continue
            out[job_id] = (str(row.get("status") or row.get("terminal_status") or ""), bool(row.get("synthetic_closeout")))
        return out
    base_map = latest_status(baseline.receipts)
    cand_map = latest_status(candidate.receipts)
    mismatches = []
    for job_id, base_value in base_map.items():
        cand_value = cand_map.get(job_id)
        if cand_value is None:
            mismatches.append(f"{job_id} missing candidate terminal receipt")
            continue
        if base_value != cand_value:
            mismatches.append(f"{job_id} terminal parity drifted baseline={base_value!r} candidate={cand_value!r}")
    if mismatches:
        return _fail("terminal_retirement", "terminal receipt or synthetic closeout parity drifted", *mismatches[:12])
    return _pass("terminal_retirement", f"terminal receipt parity matched for {len(base_map)} jobs")


def _compare_continuity_restart(baseline: SchedulerSnapshot, candidate: SchedulerSnapshot) -> BenchmarkDimensionResult:
    if not baseline.receipts or not candidate.receipts:
        return _pending("continuity_restart", "missing receipts for restart/recovery comparison")
    base_recovery = sum(1 for row in baseline.receipts if str(row.get("recovery_reason") or ""))
    cand_recovery = sum(1 for row in candidate.receipts if str(row.get("recovery_reason") or ""))
    if base_recovery != cand_recovery:
        return _fail(
            "continuity_restart",
            "recovery receipt count drifted",
            f"baseline recovery receipts={base_recovery}",
            f"candidate recovery receipts={cand_recovery}",
        )
    return _pass("continuity_restart", f"recovery receipt count matched at {base_recovery}")


def _compare_closeout_promotion(baseline: SchedulerSnapshot, candidate: SchedulerSnapshot) -> BenchmarkDimensionResult:
    if not baseline.family_records or not candidate.family_records:
        return _pending("closeout_promotion", "missing family records for closeout comparison")
    base_map = {str(row.get("parent_uid") or ""): row for row in baseline.family_records}
    cand_map = {str(row.get("parent_uid") or ""): row for row in candidate.family_records}
    mismatches = []
    for parent_uid, base_row in base_map.items():
        cand_row = cand_map.get(parent_uid)
        if not cand_row:
            mismatches.append(f"{parent_uid} missing candidate family record")
            continue
        for field in ("intent_validation_child_uid", "required_final_child_uid"):
            if str(base_row.get(field) or "") != str(cand_row.get(field) or ""):
                mismatches.append(f"{parent_uid} {field} drifted")
    if mismatches:
        return _fail("closeout_promotion", "family closeout ownership drifted", *mismatches[:12])
    return _pass("closeout_promotion", f"family closeout ownership matched for {len(base_map)} parent families")


def _compare_complexity(
    baseline_metrics: CodeMetrics | None,
    candidate_metrics: CodeMetrics | None,
    budget: Mapping[str, float],
) -> BenchmarkDimensionResult:
    if baseline_metrics is None or candidate_metrics is None:
        return _pending("complexity", "missing code metrics for one or both schedulers")
    failures = []
    if candidate_metrics.file_count > baseline_metrics.file_count:
        failures.append(f"file_count grew {baseline_metrics.file_count} -> {candidate_metrics.file_count}")
    if candidate_metrics.nonempty_line_count > baseline_metrics.nonempty_line_count:
        failures.append(
            f"nonempty_line_count grew {baseline_metrics.nonempty_line_count} -> {candidate_metrics.nonempty_line_count}"
        )
    if candidate_metrics.branch_count > baseline_metrics.branch_count:
        failures.append(f"branch_count grew {baseline_metrics.branch_count} -> {candidate_metrics.branch_count}")
    target_pct = float(budget.get("target_line_reduction_pct") or 0.0)
    if target_pct > 0.0:
        actual_pct = 0.0
        if baseline_metrics.nonempty_line_count > 0:
            actual_pct = (
                (baseline_metrics.nonempty_line_count - candidate_metrics.nonempty_line_count)
                / baseline_metrics.nonempty_line_count
            ) * 100.0
        if actual_pct + 1e-9 < target_pct:
            failures.append(f"line reduction target missed {actual_pct:.1f}% < {target_pct:.1f}%")
    if failures:
        return _fail("complexity", "candidate did not beat or match the baseline complexity budget", *failures)
    return _pass(
        "complexity",
        "candidate met the complexity budget",
        f"files {baseline_metrics.file_count} -> {candidate_metrics.file_count}",
        f"lines {baseline_metrics.nonempty_line_count} -> {candidate_metrics.nonempty_line_count}",
        f"branches {baseline_metrics.branch_count} -> {candidate_metrics.branch_count}",
    )


def _compare_stress_volume(baseline: SchedulerSnapshot, candidate: SchedulerSnapshot) -> BenchmarkDimensionResult:
    if not baseline.jobs or not candidate.jobs:
        return _pending("stress_volume", "missing synthetic stress job snapshots")
    if len(candidate.jobs) != len(baseline.jobs):
        return _fail("stress_volume", "candidate stress job count drifted", f"baseline={len(baseline.jobs)} candidate={len(candidate.jobs)}")
    graph = next((row for row in candidate.compiled_graphs if isinstance(row, Mapping)), {})
    if not bool(graph.get("single_compiled_graph")):
        return _fail("stress_volume", "candidate did not expose one compiled graph for the stress family")
    return _pass("stress_volume", f"stress fixture covered {len(candidate.jobs)} jobs", f"topology={graph.get('topology', '')}")


def _compare_destructive_conditions(baseline: SchedulerSnapshot, candidate: SchedulerSnapshot) -> BenchmarkDimensionResult:
    if not candidate.continuity_records:
        return _pending("destructive_conditions", "missing continuity records for destructive-condition comparison")
    failures = []
    allowed = {"none", "resume", "rerun", "block", "fail"}
    for row in candidate.continuity_records:
        outcome = str(row.get("recovery_outcome") or "")
        if outcome not in allowed:
            failures.append(f"invalid recovery outcome {outcome!r}")
        if bool(row.get("invented_work")):
            failures.append("continuity invented work")
        if not bool(row.get("resume_rerun_block_fail_only")):
            failures.append("continuity escaped resume/rerun/block/fail policy")
        if not bool(row.get("canonical_truth_wins")):
            failures.append("canonical truth did not win over corrupted projection")
    if failures:
        return _fail("destructive_conditions", "destructive-condition policy failed", *failures[:12])
    return _pass("destructive_conditions", f"destructive-condition policy passed for {len(candidate.continuity_records)} records")


def _compare_ownership_isolation(baseline: SchedulerSnapshot, candidate: SchedulerSnapshot) -> BenchmarkDimensionResult:
    notes = set(str(item) for item in candidate.notes)
    if "canonical_write_allowed=false" not in notes:
        return _fail("ownership_isolation", "candidate did not prove canonical writes are blocked in non-primary mode")
    if "public_port_policy=no_new_public_port" not in notes:
        return _fail("ownership_isolation", "candidate did not prove no-new-public-port policy")
    for row in candidate.closeout_records:
        if str(row.get("single_owner") or "") != "intent_family_closeout":
            return _fail("ownership_isolation", "closeout owner drifted")
        if bool(row.get("stored_derived_metadata_canonical")):
            return _fail("ownership_isolation", "derived metadata was treated as canonical")
    return _pass("ownership_isolation", "candidate kept v2 isolated behind ownership mode and closeout-only promotion")


def _compare_internal_wiring(baseline: SchedulerSnapshot, candidate: SchedulerSnapshot) -> BenchmarkDimensionResult:
    required = {"watch", "api", "web", "mobile"}
    surfaces = {str(key) for key in candidate.surface_projections}
    missing = sorted(required - surfaces)
    if missing:
        return _fail("internal_wiring", "candidate missing operator surfaces", "missing: " + ", ".join(missing))
    reference = list(candidate.surface_projections.get("api") or [])
    mismatches = []
    for name in sorted(required - {"api"}):
        if list(candidate.surface_projections.get(name) or []) != reference:
            mismatches.append(f"{name} projection differs from api")
    if mismatches:
        return _fail("internal_wiring", "projection equivalence drifted across surfaces", *mismatches)
    return _pass("internal_wiring", "watch, api, web, and mobile used equivalent projection rows")


def compare_snapshots(
    *,
    baseline: SchedulerSnapshot,
    candidate: SchedulerSnapshot,
    scenario: ScenarioSpec,
) -> tuple[BenchmarkDimensionResult, ...]:
    results = []
    for dimension in scenario.required_dimensions:
        if dimension == "operator_truth":
            results.append(_compare_operator_truth(baseline, candidate))
        elif dimension == "dependency_admission":
            results.append(_compare_dependency_admission(baseline, candidate))
        elif dimension == "terminal_retirement":
            results.append(_compare_terminal_retirement(baseline, candidate))
        elif dimension == "continuity_restart":
            results.append(_compare_continuity_restart(baseline, candidate))
        elif dimension == "closeout_promotion":
            results.append(_compare_closeout_promotion(baseline, candidate))
        elif dimension == "complexity":
            results.append(_compare_complexity(baseline.code_metrics, candidate.code_metrics, scenario.complexity_budget))
        elif dimension == "stress_volume":
            results.append(_compare_stress_volume(baseline, candidate))
        elif dimension == "destructive_conditions":
            results.append(_compare_destructive_conditions(baseline, candidate))
        elif dimension == "ownership_isolation":
            results.append(_compare_ownership_isolation(baseline, candidate))
        elif dimension == "internal_wiring":
            results.append(_compare_internal_wiring(baseline, candidate))
        else:
            results.append(_pending(dimension, "no comparator implemented for this dimension yet"))
    return tuple(results)


def summarize_status(results: Sequence[BenchmarkDimensionResult]) -> str:
    verdicts = {item.verdict for item in results}
    if "fail" in verdicts:
        return "fail"
    if "pending_interface" in verdicts:
        return "pending_interface"
    return "pass"
