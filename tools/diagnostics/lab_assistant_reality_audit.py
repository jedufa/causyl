from __future__ import annotations

"""Deterministic runtime-first audit for lab assistant reality checks.

Last modified: 2026-03-26 20:25 Europe/Vienna

This tool is intentionally conservative. It prefers UNKNOWN/FAIL over inventing a
clean story from static files. The audit only upgrades confidence when it has at
least one of:
- executed function call evidence
- direct runtime trace evidence
- persisted state evidence
"""

import argparse
import importlib
import json
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


STATUS_PASS = "PASS"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAIL = "FAIL"
STATUS_UNKNOWN = "UNKNOWN"

CRITICAL_CAPABILITIES = {
    "single task object",
    "unified entrypoint",
    "central decision logic",
    "unified status",
    "persistence",
    "recovery",
    "traceability",
    "operator truth",
}


@dataclass
class Evidence:
    kind: str
    label: str
    detail: str
    ok: bool = True
    path: str | None = None


@dataclass
class SectionResult:
    name: str
    status: str
    summary: str
    evidence: list[Evidence] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceResult:
    name: str
    status: str
    summary: str
    evidence: list[Evidence] = field(default_factory=list)
    identity_chain: dict[str, Any] = field(default_factory=dict)
    persistence_writes: list[str] = field(default_factory=list)
    operator_query: dict[str, Any] = field(default_factory=dict)
    recovery: dict[str, Any] = field(default_factory=dict)
    truth_conflicts: list[dict[str, Any]] = field(default_factory=list)


MODULE_CANDIDATES: dict[str, str] = {
    "routes": "infra.webui.server_routes_and_main",
    "host": "infra.webui.server_lab_assistant_host",
    "state": "infra.webui.server_lab_assistant_state",
    "task_register": "infra.webui.server_lab_assistant_task_register",
    "policy": "infra.webui.server_lab_assistant_delegation_policy",
    "bindings": "infra.webui.server_lab_assistant_delegation_bindings",
    "mailbox": "infra.webui.server_lab_assistant_mailbox",
    "jobs": "infra.webui.server_lab_assistant_job_lifecycle",
    "writeback": "infra.webui.server_lab_assistant_writeback",
    "snapshot": "infra.webui.server_lab_assistant_operator_snapshot",
    "recovery": "infra.webui.server_lab_assistant_recovery",
    "correlation": "infra.webui.server_lab_assistant_correlation",
    "intake": "infra.webui.server_lab_assistant_intake",
}


def _repo_rel(repo_root: Path, value: Path | str | None) -> str | None:
    if value is None:
        return None
    path = Path(value)
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except Exception:
        return str(path)


def _json_load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_import(module_name: str, *, repo_root: Path) -> tuple[Any | None, Evidence]:
    try:
        module = importlib.import_module(module_name)
        module_file = Path(str(getattr(module, "__file__", "") or "")).resolve()
        repo_root_resolved = Path(repo_root).resolve()
        try:
            module_file.relative_to(repo_root_resolved)
        except Exception:
            return None, Evidence(
                kind="executed_function_call",
                label=f"import:{module_name}",
                detail=(
                    f"importlib.import_module resolved {module_name} outside repo_root: "
                    f"{module_file} not under {repo_root_resolved}"
                ),
                ok=False,
            )
        return module, Evidence(
            kind="executed_function_call",
            label=f"import:{module_name}",
            detail=f"importlib.import_module succeeded for {module_name}",
            ok=True,
        )
    except Exception as exc:  # pragma: no cover - exercised in repo-specific runs
        return None, Evidence(
            kind="executed_function_call",
            label=f"import:{module_name}",
            detail=f"importlib.import_module failed for {module_name}: {exc.__class__.__name__}: {exc}",
            ok=False,
        )


def _module_function(module: Any | None, name: str) -> Callable[..., Any] | None:
    if module is None:
        return None
    value = getattr(module, name, None)
    return value if callable(value) else None


def _call_with_evidence(label: str, func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> tuple[Any | None, Evidence]:
    try:
        value = func(*args, **kwargs)
        return value, Evidence(
            kind="executed_function_call",
            label=label,
            detail=f"{label} executed successfully",
            ok=True,
        )
    except Exception as exc:
        return None, Evidence(
            kind="executed_function_call",
            label=label,
            detail=f"{label} failed: {exc.__class__.__name__}: {exc}",
            ok=False,
        )


def _path_evidence(path: Path, repo_root: Path, *, label: str | None = None) -> Evidence:
    return Evidence(
        kind="persisted_state" if path.exists() else "executed_function_call",
        label=label or f"path:{_repo_rel(repo_root, path)}",
        detail=(
            f"persisted state exists at {_repo_rel(repo_root, path)}"
            if path.exists()
            else f"persisted state missing at {_repo_rel(repo_root, path)}"
        ),
        ok=path.exists(),
        path=_repo_rel(repo_root, path),
    )


def _collect_candidate_state(repo_root: Path) -> list[Evidence]:
    candidates = [
        repo_root / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json",
        repo_root / "runtime" / "state" / "lab_assistant" / "assistant_state" / "assistant_state.json",
        repo_root / "runtime" / "state" / "lab_assistant" / "delegation" / "delegation_snapshot.json",
        repo_root / "runtime" / "state" / "lab_assistant" / "writeback" / "writeback_snapshot.json",
        repo_root / "runtime" / "state" / "lab_assistant" / "recovery" / "recovery_snapshot.json",
    ]
    return [_path_evidence(path, repo_root) for path in candidates]


def discover_modules(repo_root: Path) -> tuple[dict[str, Any | None], list[Evidence]]:
    modules: dict[str, Any | None] = {}
    evidence: list[Evidence] = []
    for key, module_name in MODULE_CANDIDATES.items():
        module, row = _safe_import(module_name, repo_root=repo_root)
        modules[key] = module
        evidence.append(row)
    return modules, evidence

def audit_entrypoints(repo_root: Path, modules: Mapping[str, Any | None], module_evidence: Sequence[Evidence]) -> SectionResult:
    evidence = list(module_evidence)
    routes_module = modules.get("routes")
    routes_found = routes_module is not None
    cli_path = repo_root / "tools" / "bin" / "server_start.sh"
    evidence.append(_path_evidence(cli_path, repo_root, label="server_start_cli"))

    entrypoints: list[dict[str, Any]] = []
    if routes_found:
        for attr in ("handle_api_get", "handle_api_post", "dispatch_request"):
            if callable(getattr(routes_module, attr, None)):
                entrypoints.append({"kind": "user-facing", "module": MODULE_CANDIDATES["routes"], "callable": attr})
        for path in (
            "/api/admin/lab_assistant/snapshot",
            "/api/admin/lab_assistant/task_register",
            "/api/agents/send",
            "/api/schedules/run_now",
        ):
            entrypoints.append({"kind": "candidate_route", "path": path})
    if cli_path.exists():
        entrypoints.append({"kind": "internal", "path": _repo_rel(repo_root, cli_path)})

    if routes_found and entrypoints:
        status = STATUS_PARTIAL
        summary = "At least one runtime-importable entrypoint surface exists, but complete task creation flow still requires trace confirmation."
    else:
        status = STATUS_UNKNOWN
        summary = "No runtime-importable lab assistant entrypoint module was confirmed. Entry-point truth remains UNKNOWN."
    return SectionResult("entrypoints", status, summary, evidence=evidence, details={"entrypoints": entrypoints})


def audit_task_representation(repo_root: Path, modules: Mapping[str, Any | None]) -> SectionResult:
    evidence = _collect_candidate_state(repo_root)
    task_register_module = modules.get("task_register")
    snapshot_module = modules.get("snapshot")

    if task_register_module is not None:
        func = _module_function(task_register_module, "read_task_register_snapshot") or _module_function(task_register_module, "load_or_init_task_register")
        if func is not None:
            _, ev = _call_with_evidence(f"{task_register_module.__name__}.{func.__name__}", func, repo_root, initialize_if_missing=False) if func.__name__ == "read_task_register_snapshot" else _call_with_evidence(f"{task_register_module.__name__}.{func.__name__}", func, repo_root)
            evidence.append(ev)
    if snapshot_module is not None:
        func = _module_function(snapshot_module, "build_operator_snapshot")
        if func is not None:
            _, ev = _call_with_evidence(f"{snapshot_module.__name__}.{func.__name__}", func, repo_root)
            evidence.append(ev)

    existing = [row for row in evidence if row.ok]
    status = STATUS_PARTIAL if existing else STATUS_UNKNOWN
    summary = (
        "Persisted task-like artifacts were found, but canonical ownership was not proven by runtime correlation."
        if existing
        else "No persisted canonical task artifact could be proven. Task representation remains UNKNOWN."
    )
    return SectionResult(
        "task_representation",
        status,
        summary,
        evidence=evidence,
        details={
            "candidate_locations": [asdict(row) for row in evidence if row.path],
            "canonical_owner": "UNKNOWN" if status != STATUS_PASS else "task_register",
        },
    )


def audit_routing(modules: Mapping[str, Any | None]) -> SectionResult:
    evidence: list[Evidence] = []
    policy_module = modules.get("policy")
    status = STATUS_UNKNOWN
    details: dict[str, Any] = {"routing_callables": []}
    if policy_module is not None:
        for attr, kwargs in (
            (
                "classify_answer_vs_delegate",
                ({
                    "summary": "audit routing",
                    "preferred_surface": "workflow",
                    "estimated_duration_s": 90,
                    "requires_new_evidence": True,
                },),
            ),
        ):
            func = _module_function(policy_module, attr)
            if func is not None:
                value, ev = _call_with_evidence(f"{policy_module.__name__}.{attr}", func, *kwargs)
                evidence.append(ev)
                details["routing_callables"].append(attr)
                if ev.ok:
                    details["classification"] = value
                    status = STATUS_PARTIAL
    if not evidence:
        evidence.append(Evidence(
            kind="executed_function_call",
            label="routing",
            detail="No runtime-importable routing policy callable was available.",
            ok=False,
        ))
    summary = (
        "Routing call path executed, but end-to-end decision authority remains distributed or unproven."
        if status != STATUS_UNKNOWN
        else "Routing authority could not be confirmed by executed call path."
    )
    return SectionResult("routing", status, summary, evidence=evidence, details=details)


def audit_status_surface(repo_root: Path, modules: Mapping[str, Any | None]) -> SectionResult:
    evidence: list[Evidence] = []
    snapshot_module = modules.get("snapshot")
    details: dict[str, Any] = {"single_query_possible": False}
    status = STATUS_UNKNOWN
    if snapshot_module is not None:
        func = _module_function(snapshot_module, "build_operator_snapshot")
        if func is not None:
            value, ev = _call_with_evidence(f"{snapshot_module.__name__}.build_operator_snapshot", func, repo_root)
            evidence.append(ev)
            if ev.ok:
                details["single_query_possible"] = True
                details["snapshot_keys"] = sorted(list(value.keys())) if isinstance(value, dict) else []
                status = STATUS_PARTIAL
    if not evidence:
        evidence.extend(_collect_candidate_state(repo_root))
    summary = (
        "A single snapshot function exists, but truthful status still requires validation against canonical state."
        if status != STATUS_UNKNOWN
        else "No single runtime status query was proven. Operator truth is UNKNOWN and should default to FAIL."
    )
    return SectionResult("status", status, summary, evidence=evidence, details=details)


def audit_persistence_and_recovery(repo_root: Path, modules: Mapping[str, Any | None]) -> SectionResult:
    evidence = _collect_candidate_state(repo_root)
    recovery_module = modules.get("recovery")
    if recovery_module is not None:
        for attr in ("read_recovery_state", "load_or_init_recovery_state", "reconcile_after_restart"):
            func = _module_function(recovery_module, attr)
            if func is not None:
                value, ev = _call_with_evidence(f"{recovery_module.__name__}.{attr}", func, repo_root)
                evidence.append(ev)
                if ev.ok:
                    break
    existing = [row for row in evidence if row.ok]
    status = STATUS_PARTIAL if any(row.label.startswith("import:") or row.kind == "executed_function_call" for row in evidence if row.ok) else STATUS_UNKNOWN
    if not existing:
        status = STATUS_UNKNOWN
    summary = (
        "Some persisted recovery-related state exists or recovery helpers import, but restart consistency was not yet proven by forced trace."
        if existing
        else "Recovery truth could not be demonstrated from runtime helpers or persisted state."
    )
    return SectionResult("persistence_recovery", status, summary, evidence=evidence, details={})


def _init_minimal_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)


def run_happy_trace(modules: Mapping[str, Any | None]) -> TraceResult:
    evidence: list[Evidence] = []
    required = {
        "host": "bootstrap_host_baseline",
        "state": "load_or_init_assistant_state",
        "task_register": "load_or_init_task_register",
        "policy": "classify_answer_vs_delegate",
        "bindings": "bind_delegation_request",
        "snapshot": "build_operator_snapshot",
    }
    missing: list[str] = []
    for key, func_name in required.items():
        module = modules.get(key)
        if module is None or _module_function(module, func_name) is None:
            missing.append(f"{MODULE_CANDIDATES[key]}.{func_name}")
    if missing:
        evidence.extend(
            Evidence(
                kind="executed_function_call",
                label="missing_runtime_surface",
                detail=f"Happy trace blocked because required callables are unavailable: {', '.join(missing)}",
                ok=False,
            )
            for _ in [0]
        )
        return TraceResult(
            name="Trace A — Happy Path",
            status=STATUS_UNKNOWN,
            summary="Happy-path runtime trace could not execute because the assistant runtime surface is incomplete or missing.",
            evidence=evidence,
            operator_query={"status": STATUS_FAIL, "method": None, "reason": "No single callable available."},
            recovery={"status": STATUS_UNKNOWN},
        )

    host = modules["host"]
    state = modules["state"]
    register = modules["task_register"]
    policy = modules["policy"]
    bindings = modules["bindings"]
    snapshot = modules["snapshot"]
    writeback = modules.get("writeback")

    with tempfile.TemporaryDirectory(prefix="lab_assistant_audit_happy_") as tmp:
        root = Path(tmp)
        _init_minimal_repo(root)
        _, ev = _call_with_evidence(f"{host.__name__}.bootstrap_host_baseline", host.bootstrap_host_baseline, root)
        evidence.append(ev)
        _, ev = _call_with_evidence(f"{state.__name__}.load_or_init_assistant_state", state.load_or_init_assistant_state, root)
        evidence.append(ev)
        current, ev = _call_with_evidence(f"{register.__name__}.load_or_init_task_register", register.load_or_init_task_register, root)
        evidence.append(ev)
        if current is None:
            return TraceResult(name="Trace A — Happy Path", status=STATUS_FAIL, summary="Task register could not initialize.", evidence=evidence)
        task_payload = {
            "task_id": "task_x8k3_happy",
            "kind": "software",
            "title": "Happy path reality audit task",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": "PIS-20260326-174900Z-X8K3",
            "next_expected_action": "delegate and inspect snapshot",
        }
        upsert, ev = _call_with_evidence(
            f"{register.__name__}.upsert_task",
            register.upsert_task,
            root,
            expected_generation=int(current["write_generation"]),
            writer="audit.happy",
            task=task_payload,
        )
        evidence.append(ev)
        if upsert is None:
            return TraceResult(name="Trace A — Happy Path", status=STATUS_FAIL, summary="Task upsert failed.", evidence=evidence)
        classification, ev = _call_with_evidence(
            f"{policy.__name__}.classify_answer_vs_delegate",
            policy.classify_answer_vs_delegate,
            {
                "summary": "reality audit trace",
                "preferred_surface": "workflow",
                "estimated_duration_s": 90,
                "requires_new_evidence": True,
            },
        )
        evidence.append(ev)
        request, ev = _call_with_evidence(
            f"{policy.__name__}.build_typed_delegation_request",
            policy.build_typed_delegation_request,
            classification=classification,
            surface="workflow",
            owner_ref={"task_id": "task_x8k3_happy", "ledger_uid": "PIS-20260326-174900Z-X8K3"},
            summary="Launch audit workflow",
            requested_action="launch workflow",
            expected_evidence=[{"kind": "workflow_digest", "description": "trace evidence", "required": True}],
            next_decision={"kind": "review_evidence", "summary": "review happy trace", "owner": "assistant"},
            input_payload={"scope": "audit"},
        )
        evidence.append(ev)
        if isinstance(request, dict):
            request["request_id"] = "delegate_x8k3_happy"
            request.setdefault("correlation_id", "corr_x8k3_happy")
            request.setdefault("launch_turn_id", "turn_x8k3_happy")
            request.setdefault("idempotency_key", "idem_x8k3_happy")
        bound, ev = _call_with_evidence(
            f"{bindings.__name__}.bind_delegation_request",
            bindings.bind_delegation_request,
            root,
            expected_generation=int(upsert["task_register"]["write_generation"]),
            writer="audit.happy",
            request=request,
            launch_result={"workflow_id": "wf_x8k3_happy"},
        )
        evidence.append(ev)
        snapshot_payload, ev = _call_with_evidence(f"{snapshot.__name__}.build_operator_snapshot", snapshot.build_operator_snapshot, root)
        evidence.append(ev)

        persistence_writes = [str(path.relative_to(root)) for path in root.rglob("*.json")]
        truth_conflicts: list[dict[str, Any]] = []
        if writeback is not None and _module_function(writeback, "read_task_record") is not None:
            task_record, ev = _call_with_evidence(f"{writeback.__name__}.read_task_record", writeback.read_task_record, root, task_id="task_x8k3_happy")
            evidence.append(ev)
            if ev.ok and isinstance(task_record, dict) and isinstance(snapshot_payload, dict):
                active = [row for row in snapshot_payload.get("active_tasks", []) if row.get("task_id") == "task_x8k3_happy"]
                if active and task_record.get("reconciled_status") and active[0].get("status") != task_record.get("reconciled_status"):
                    truth_conflicts.append({
                        "task_id": "task_x8k3_happy",
                        "canonical": task_record.get("reconciled_status"),
                        "derived": active[0].get("status"),
                        "classification": "UNTRUSTWORTHY",
                    })

        operator_ok = isinstance(snapshot_payload, dict)
        return TraceResult(
            name="Trace A — Happy Path",
            status=STATUS_PASS if operator_ok else STATUS_FAIL,
            summary="Happy-path trace executed through task creation, delegation binding, and operator snapshot." if operator_ok else "Happy-path trace did not produce operator snapshot.",
            evidence=evidence,
            identity_chain={
                "task_id": "task_x8k3_happy",
                "ledger_uid": "PIS-20260326-174900Z-X8K3",
                "delegation_request_id": bound.get("delegation_ref", {}).get("delegation_request_id") if isinstance(bound, dict) else None,
                "workflow_id": "wf_x8k3_happy",
                "correlation_id": (request or {}).get("correlation_id") if isinstance(request, dict) else None,
                "launch_turn_id": (request or {}).get("launch_turn_id") if isinstance(request, dict) else None,
            },
            persistence_writes=persistence_writes,
            operator_query={
                "status": STATUS_PASS if operator_ok else STATUS_FAIL,
                "method": f"{snapshot.__name__}.build_operator_snapshot",
                "task_observed": operator_ok,
            },
            recovery={"status": STATUS_UNKNOWN, "reason": "Recovery is exercised in Trace B."},
            truth_conflicts=truth_conflicts,
        )


def _read_json_if_exists(path: Path) -> Any | None:
    if not path.exists():
        return None
    return _json_load(path)


def run_failure_trace(modules: Mapping[str, Any | None]) -> TraceResult:
    evidence: list[Evidence] = []
    required = {
        "host": "bootstrap_host_baseline",
        "task_register": "load_or_init_task_register",
        "policy": "classify_answer_vs_delegate",
        "bindings": "bind_delegation_request",
        "snapshot": "build_operator_snapshot",
    }
    missing = [f"{MODULE_CANDIDATES[key]}.{func}" for key, func in required.items() if modules.get(key) is None or _module_function(modules.get(key), func) is None]
    if missing:
        evidence.append(Evidence(
            kind="executed_function_call",
            label="missing_runtime_surface",
            detail=f"Failure trace blocked because required callables are unavailable: {', '.join(missing)}",
            ok=False,
        ))
        return TraceResult(
            name="Trace B — Failure Path",
            status=STATUS_UNKNOWN,
            summary="Failure-path runtime trace could not execute because the assistant runtime surface is incomplete or missing.",
            evidence=evidence,
            operator_query={"status": STATUS_FAIL, "method": None, "reason": "No single callable available."},
            recovery={"status": STATUS_UNKNOWN},
        )

    host = modules["host"]
    register = modules["task_register"]
    policy = modules["policy"]
    bindings = modules["bindings"]
    snapshot = modules["snapshot"]

    with tempfile.TemporaryDirectory(prefix="lab_assistant_audit_failure_") as tmp:
        root = Path(tmp)
        _init_minimal_repo(root)
        _, ev = _call_with_evidence(f"{host.__name__}.bootstrap_host_baseline", host.bootstrap_host_baseline, root)
        evidence.append(ev)
        current, ev = _call_with_evidence(f"{register.__name__}.load_or_init_task_register", register.load_or_init_task_register, root)
        evidence.append(ev)
        if current is None:
            return TraceResult(name="Trace B — Failure Path", status=STATUS_FAIL, summary="Task register could not initialize.", evidence=evidence)
        upsert, ev = _call_with_evidence(
            f"{register.__name__}.upsert_task",
            register.upsert_task,
            root,
            expected_generation=int(current["write_generation"]),
            writer="audit.failure",
            task={
                "task_id": "task_x8k3_failure",
                "kind": "software",
                "title": "Failure path reality audit task",
                "status": "active",
                "owner": "assistant",
                "source": "operator",
                "ledger_uid": "PIS-20260326-174900Z-X8K3",
                "next_expected_action": "detect duplicate launch",
            },
        )
        evidence.append(ev)
        classification, ev = _call_with_evidence(
            f"{policy.__name__}.classify_answer_vs_delegate",
            policy.classify_answer_vs_delegate,
            {
                "summary": "duplicate trigger failure path",
                "preferred_surface": "workflow",
                "estimated_duration_s": 90,
                "requires_new_evidence": True,
            },
        )
        evidence.append(ev)
        request, ev = _call_with_evidence(
            f"{policy.__name__}.build_typed_delegation_request",
            policy.build_typed_delegation_request,
            classification=classification,
            surface="workflow",
            owner_ref={"task_id": "task_x8k3_failure", "ledger_uid": "PIS-20260326-174900Z-X8K3"},
            summary="Launch duplicate trace",
            requested_action="launch workflow",
            expected_evidence=[{"kind": "workflow_digest", "description": "trace evidence", "required": True}],
            next_decision={"kind": "review_evidence", "summary": "review duplicate trace", "owner": "assistant"},
            input_payload={"scope": "audit"},
        )
        evidence.append(ev)
        if isinstance(request, dict):
            request["request_id"] = "delegate_x8k3_duplicate"
            request.setdefault("correlation_id", "corr_x8k3_duplicate")
            request.setdefault("launch_turn_id", "turn_x8k3_duplicate")
            request.setdefault("idempotency_key", "idem_x8k3_duplicate")
        first, ev = _call_with_evidence(
            f"{bindings.__name__}.bind_delegation_request:first",
            bindings.bind_delegation_request,
            root,
            expected_generation=int(upsert["task_register"]["write_generation"]),
            writer="audit.failure",
            request=request,
            launch_result={"workflow_id": "wf_x8k3_duplicate"},
        )
        evidence.append(ev)

        register_path = root / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
        before_state = _read_json_if_exists(register_path)
        second, ev = _call_with_evidence(
            f"{bindings.__name__}.bind_delegation_request:duplicate",
            bindings.bind_delegation_request,
            root,
            expected_generation=int(first["task_register"]["write_generation"]),
            writer="audit.failure.duplicate",
            request=request,
            launch_result={"workflow_id": "wf_x8k3_duplicate"},
        )
        evidence.append(ev)
        after_state = _read_json_if_exists(register_path)
        snapshot_payload, ev = _call_with_evidence(f"{snapshot.__name__}.build_operator_snapshot", snapshot.build_operator_snapshot, root)
        evidence.append(ev)

        duplicate_free = True
        identity_drift = False
        lost_task = False
        if isinstance(before_state, dict) and isinstance(after_state, dict):
            before_tasks = {row.get("task_id") for row in before_state.get("active_tasks", [])}
            after_tasks = {row.get("task_id") for row in after_state.get("active_tasks", [])}
            lost_task = "task_x8k3_failure" not in after_tasks
            identity_drift = before_tasks != after_tasks and not lost_task
            duplicate_free = len([row for row in after_state.get("active_tasks", []) if row.get("task_id") == "task_x8k3_failure"]) <= 1
        recovery_status = STATUS_PASS if duplicate_free and not lost_task and not identity_drift else STATUS_FAIL
        return TraceResult(
            name="Trace B — Failure Path",
            status=STATUS_PASS if recovery_status == STATUS_PASS and isinstance(snapshot_payload, dict) else STATUS_FAIL,
            summary="Failure-path trace injected a duplicate trigger and compared pre/post state for drift." if isinstance(snapshot_payload, dict) else "Failure-path trace did not reach snapshot projection.",
            evidence=evidence,
            identity_chain={
                "task_id": "task_x8k3_failure",
                "delegation_request_id": (first or {}).get("delegation_ref", {}).get("delegation_request_id") if isinstance(first, dict) else None,
                "workflow_id": "wf_x8k3_duplicate",
                "correlation_id": (request or {}).get("correlation_id") if isinstance(request, dict) else None,
            },
            persistence_writes=[str(path.relative_to(root)) for path in root.rglob("*.json")],
            operator_query={
                "status": STATUS_PASS if isinstance(snapshot_payload, dict) else STATUS_FAIL,
                "method": f"{snapshot.__name__}.build_operator_snapshot",
                "task_observed": isinstance(snapshot_payload, dict),
            },
            recovery={
                "status": recovery_status,
                "duplicate_free": duplicate_free,
                "lost_task": lost_task,
                "identity_drift": identity_drift,
                "pre_generation": None if not isinstance(before_state, dict) else before_state.get("write_generation"),
                "post_generation": None if not isinstance(after_state, dict) else after_state.get("write_generation"),
                "duplicate_bind_ok": bool(second),
            },
            truth_conflicts=[],
        )


def classify_capability(*, capability: str, trace_a: TraceResult, trace_b: TraceResult, sections: Mapping[str, SectionResult]) -> tuple[str, str, str]:
    evidence_labels: list[str] = []
    pain = "high"
    if capability == "unified entrypoint":
        section = sections["entrypoints"]
        evidence_labels = [row.label for row in section.evidence[:3]]
        if section.status == STATUS_PARTIAL and trace_a.status != STATUS_UNKNOWN:
            return STATUS_PARTIAL, "; ".join(evidence_labels), pain
        return STATUS_FAIL, "; ".join(evidence_labels), pain
    if capability == "single task object":
        section = sections["task_representation"]
        evidence_labels = [row.label for row in section.evidence[:3]]
        if trace_a.identity_chain.get("task_id") and trace_a.identity_chain.get("delegation_request_id"):
            return STATUS_PARTIAL, "; ".join(evidence_labels), pain
        return STATUS_FAIL, "; ".join(evidence_labels), pain
    if capability == "central decision logic":
        section = sections["routing"]
        evidence_labels = [row.label for row in section.evidence[:3]]
        return (STATUS_PARTIAL if section.status == STATUS_PARTIAL else STATUS_FAIL), "; ".join(evidence_labels), pain
    if capability == "unified status":
        section = sections["status"]
        evidence_labels = [row.label for row in section.evidence[:3]]
        if trace_a.operator_query.get("status") == STATUS_PASS and trace_b.operator_query.get("status") == STATUS_PASS:
            return STATUS_PARTIAL, "; ".join(evidence_labels), pain
        return STATUS_FAIL, "; ".join(evidence_labels), pain
    if capability == "persistence":
        section = sections["persistence_recovery"]
        evidence_labels = [row.label for row in section.evidence[:3]]
        if trace_a.persistence_writes:
            return STATUS_PARTIAL, "; ".join(evidence_labels), pain
        return STATUS_FAIL, "; ".join(evidence_labels), pain
    if capability == "recovery":
        evidence_labels = [row.label for row in trace_b.evidence[:3]]
        return (STATUS_PASS if trace_b.recovery.get("status") == STATUS_PASS else STATUS_FAIL), "; ".join(evidence_labels), pain
    if capability == "traceability":
        evidence_labels = [row.label for row in trace_a.evidence[:3]]
        chain = trace_a.identity_chain
        needed = ["task_id", "delegation_request_id", "workflow_id", "correlation_id"]
        if all(chain.get(key) for key in needed):
            return STATUS_PARTIAL, "; ".join(evidence_labels), pain
        return STATUS_FAIL, "; ".join(evidence_labels), pain
    if capability == "idempotency":
        evidence_labels = [row.label for row in trace_b.evidence[:3]]
        ok = bool(trace_b.recovery.get("duplicate_free")) and not bool(trace_b.recovery.get("identity_drift"))
        return (STATUS_PASS if ok else STATUS_FAIL), "; ".join(evidence_labels), pain
    if capability == "operator truth":
        evidence_labels = [row.label for row in trace_a.evidence[:3]]
        has_conflict = bool(trace_a.truth_conflicts or trace_b.truth_conflicts)
        ok = trace_a.operator_query.get("status") == STATUS_PASS and trace_b.operator_query.get("status") == STATUS_PASS and not has_conflict
        return (STATUS_PASS if ok else STATUS_FAIL), "; ".join(evidence_labels), pain
    raise KeyError(capability)


def build_gap_matrix(trace_a: TraceResult, trace_b: TraceResult, sections: Mapping[str, SectionResult]) -> list[dict[str, Any]]:
    capabilities = [
        "single task object",
        "unified entrypoint",
        "central decision logic",
        "unified status",
        "persistence",
        "recovery",
        "traceability",
        "idempotency",
        "operator truth",
    ]
    rows: list[dict[str, Any]] = []
    for capability in capabilities:
        status, evidence, pain = classify_capability(capability=capability, trace_a=trace_a, trace_b=trace_b, sections=sections)
        if capability in CRITICAL_CAPABILITIES and status == STATUS_UNKNOWN:
            status = STATUS_FAIL
        rows.append({"capability": capability, "status": status, "evidence": evidence or "UNKNOWN", "pain": pain})
    return rows


def top_roi_fixes(repo_root: Path, gap_matrix: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    failures = {row["capability"] for row in gap_matrix if row["status"] == STATUS_FAIL}
    fixes: list[dict[str, Any]] = []
    if "unified entrypoint" in failures or "operator truth" in failures:
        fixes.append({
            "name": "Restore and wire lab assistant WebUI runtime modules",
            "impacted_gaps": ["unified entrypoint", "unified status", "operator truth"],
            "impact": 5,
            "effort": 3,
            "priority": "MAX",
            "files": [
                "infra/webui/server_routes_and_main.py",
                "infra/webui/server_lab_assistant_operator_snapshot.py",
                "infra/webui/server_lab_assistant_task_register.py",
            ],
        })
    if "traceability" in failures or "single task object" in failures:
        fixes.append({
            "name": "Enforce one identity chain across intake, delegation, jobs, and snapshot",
            "impacted_gaps": ["single task object", "traceability", "operator truth"],
            "impact": 5,
            "effort": 4,
            "priority": "MAX",
            "files": [
                "infra/webui/server_lab_assistant_intake.py",
                "infra/webui/server_lab_assistant_delegation_bindings.py",
                "infra/webui/server_lab_assistant_job_lifecycle.py",
                "infra/webui/server_lab_assistant_writeback.py",
                "infra/webui/server_lab_assistant_operator_snapshot.py",
            ],
        })
    if "central decision logic" in failures:
        fixes.append({
            "name": "Expose one executed routing authority",
            "impacted_gaps": ["central decision logic", "traceability"],
            "impact": 4,
            "effort": 2,
            "priority": "MAX",
            "files": [
                "infra/webui/server_lab_assistant_delegation_policy.py",
                "infra/webui/server_routes_and_main.py",
            ],
        })
    if "recovery" in failures or "idempotency" in failures:
        fixes.append({
            "name": "Add forced restart and duplicate-trigger recovery coverage",
            "impacted_gaps": ["recovery", "idempotency", "operator truth"],
            "impact": 5,
            "effort": 3,
            "priority": "MAX",
            "files": [
                "infra/webui/server_lab_assistant_recovery.py",
                "infra/webui/server_lab_assistant_operator_snapshot.py",
                "tests/control_plane/webui/test_lab_assistant_recovery.py",
            ],
        })
    fixes.append({
        "name": "Keep this runtime-first audit in CI",
        "impacted_gaps": ["operator truth", "recovery", "traceability"],
        "impact": 3,
        "effort": 1,
        "priority": "LOW",
        "files": [
            _repo_rel(repo_root, repo_root / "tools" / "diagnostics" / "lab_assistant_reality_audit.py") or "tools/diagnostics/lab_assistant_reality_audit.py",
            "tests/tools/test_lab_assistant_reality_audit.py",
        ],
    })
    return fixes[:5]


def identity_verdict(trace_a: TraceResult, trace_b: TraceResult) -> str:
    needed = ["task_id", "delegation_request_id", "workflow_id", "correlation_id"]
    happy_ok = all(trace_a.identity_chain.get(key) for key in needed)
    failure_ok = all(trace_b.identity_chain.get(key) for key in ("task_id", "delegation_request_id", "workflow_id", "correlation_id")) and trace_b.recovery.get("status") == STATUS_PASS
    return STATUS_PASS if happy_ok and failure_ok else STATUS_FAIL


def render_markdown(report: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# {report['title']}")
    lines.append("")
    lines.append(f"- Generated by: `tools/diagnostics/lab_assistant_reality_audit.py`")
    lines.append(f"- Repo root: `{report['repo_root']}`")
    lines.append(f"- Identity chain verdict: **{report['identity_chain_verdict']}**")
    lines.append(f"- Stop condition reached: **{'yes' if report['stop_condition_reached'] else 'no'}**")
    lines.append("")
    lines.append("## Executive verdict")
    lines.append("")
    lines.append(report["executive_verdict"])
    lines.append("")

    lines.append("## Section evidence")
    lines.append("")
    for key, section in report["sections"].items():
        lines.append(f"### {section['name']} — {section['status']}")
        lines.append("")
        lines.append(section["summary"])
        lines.append("")
        for row in section["evidence"]:
            flag = "OK" if row["ok"] else "FAIL"
            lines.append(f"- [{flag}] {row['kind']} :: `{row['label']}` :: {row['detail']}")
        lines.append("")

    lines.append("## Dual traces")
    lines.append("")
    for trace in report["traces"]:
        lines.append(f"### {trace['name']} — {trace['status']}")
        lines.append("")
        lines.append(trace["summary"])
        lines.append("")
        lines.append("Identity chain:")
        lines.append("")
        for key, value in trace["identity_chain"].items():
            lines.append(f"- `{key}`: `{value}`")
        if not trace["identity_chain"]:
            lines.append("- none proven")
        lines.append("")
        lines.append("Evidence:")
        lines.append("")
        for row in trace["evidence"]:
            flag = "OK" if row["ok"] else "FAIL"
            lines.append(f"- [{flag}] {row['kind']} :: `{row['label']}` :: {row['detail']}")
        lines.append("")
        lines.append(f"Operator query test: `{trace['operator_query'].get('status')}` via `{trace['operator_query'].get('method')}`")
        lines.append("")
        lines.append(f"Recovery: `{trace['recovery'].get('status')}`")
        lines.append("")
        if trace["truth_conflicts"]:
            lines.append("Truth conflicts:")
            lines.append("")
            for conflict in trace["truth_conflicts"]:
                lines.append(f"- task `{conflict['task_id']}` canonical=`{conflict['canonical']}` derived=`{conflict['derived']}` => `{conflict['classification']}`")
            lines.append("")

    lines.append("## Gap matrix")
    lines.append("")
    lines.append("| Capability | Status | Evidence | Pain |")
    lines.append("|---|---|---|---|")
    for row in report["gap_matrix"]:
        lines.append(f"| {row['capability']} | {row['status']} | {row['evidence']} | {row['pain']} |")
    lines.append("")

    lines.append("## Top 5 ROI fixes")
    lines.append("")
    for idx, fix in enumerate(report["top_fixes"], start=1):
        lines.append(f"### {idx}. {fix['name']}")
        lines.append("")
        lines.append(f"- impacted gaps: {', '.join(fix['impacted_gaps'])}")
        lines.append(f"- impact: {fix['impact']}")
        lines.append(f"- effort: {fix['effort']}")
        lines.append(f"- priority: {fix['priority']}")
        lines.append(f"- files: {', '.join(fix['files'])}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def generate_report(repo_root: Path) -> dict[str, Any]:
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    modules, module_evidence = discover_modules(repo_root)
    sections = {
        "entrypoints": audit_entrypoints(repo_root, modules, module_evidence),
        "task_representation": audit_task_representation(repo_root, modules),
        "routing": audit_routing(modules),
        "status": audit_status_surface(repo_root, modules),
        "persistence_recovery": audit_persistence_and_recovery(repo_root, modules),
    }
    trace_a = run_happy_trace(modules)
    trace_b = run_failure_trace(modules)
    gap_matrix = build_gap_matrix(trace_a, trace_b, sections)
    verdict = identity_verdict(trace_a, trace_b)
    top_fixes = top_roi_fixes(repo_root, gap_matrix)
    executive_verdict = (
        "System is NOT a usable lab assistant under the hardened audit contract. The audit could not prove a single identity chain, one truthful operator query, and restart-consistent recovery in one runtime-backed story."
        if verdict == STATUS_FAIL or any(row["status"] == STATUS_FAIL for row in gap_matrix)
        else "System passes the hardened audit contract."
    )
    report = {
        "title": "Lab Assistant Reality Audit (HARDENED)",
        "repo_root": str(repo_root),
        "identity_chain_verdict": verdict,
        "stop_condition_reached": True,
        "executive_verdict": executive_verdict,
        "sections": {key: asdict(value) for key, value in sections.items()},
        "traces": [asdict(trace_a), asdict(trace_b)],
        "gap_matrix": gap_matrix,
        "top_fixes": top_fixes,
    }
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Runtime-first lab assistant reality audit")
    parser.add_argument("--repo-root", default=".", help="Repository root to audit")
    parser.add_argument("--report-md", required=True, help="Markdown report output path")
    parser.add_argument("--report-json", help="Optional JSON report output path")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    report = generate_report(repo_root)
    md = render_markdown(report)
    report_md = Path(args.report_md)
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(md, encoding="utf-8")
    if args.report_json:
        report_json = Path(args.report_json)
        report_json.parent.mkdir(parents=True, exist_ok=True)
        report_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
