"""Graph contracts and deterministic runner for phase-2 composed actions."""

from __future__ import annotations

import json

from .contract_source import load_contract
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple

COMPOSED_EXECUTION_GRAPH_PATH = Path("governance/contracts/composed_execution_graph_v1.json")


class GraphValidationError(ValueError):
    """Raised when a composed execution graph violates contract rules."""


def _contract_root(repo_root: Optional[Path] = None) -> Path:
    default_root = Path(__file__).resolve().parents[3]
    if repo_root is None:
        return default_root
    candidate = Path(repo_root).resolve()
    if (candidate / COMPOSED_EXECUTION_GRAPH_PATH).exists():
        return candidate
    return default_root


def load_composed_execution_graph(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _contract_root(repo_root)
    return load_contract(root / COMPOSED_EXECUTION_GRAPH_PATH)


def _graph_rows(repo_root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    rows = list(load_composed_execution_graph(repo_root=repo_root).get("graphs") or [])
    return {str(row.get("graph_id") or "").strip(): dict(row) for row in rows if str(row.get("graph_id") or "").strip()}


def validate_composed_execution_graphs(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    payload = load_composed_execution_graph(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    rows = list(payload.get("graphs") or [])
    if str(payload.get("schema_version") or "").strip() != "composed_execution_graph_v1":
        errors.append({"code": "unexpected_schema_version", "message": "composed execution graph schema mismatch"})
    for row in rows:
        graph_id = str(row.get("graph_id") or "").strip()
        terminal = str(row.get("terminal_verifier_node") or "").strip()
        nodes = {str(node.get("node_id") or "").strip(): dict(node) for node in list(row.get("nodes") or []) if str(node.get("node_id") or "").strip()}
        edges = [tuple(edge) for edge in list(row.get("edges") or []) if isinstance(edge, list) and len(edge) == 2]
        if not graph_id:
            errors.append({"code": "missing_graph_id", "message": "graph_id required"})
            continue
        if not terminal or terminal not in nodes:
            errors.append({"code": "missing_terminal_verifier_node", "message": f"{graph_id} must define a valid terminal verifier node"})
        indegree: Dict[str, int] = {node_id: 0 for node_id in nodes}
        adjacency: Dict[str, List[str]] = {node_id: [] for node_id in nodes}
        for left, right in edges:
            if left not in nodes or right not in nodes:
                errors.append({"code": "unknown_edge_node", "message": f"{graph_id} references unknown edge node"})
                continue
            adjacency[left].append(right)
            indegree[right] += 1
        queue: deque[str] = deque(sorted(node_id for node_id, count in indegree.items() if count == 0))
        visited: List[str] = []
        while queue:
            node_id = queue.popleft()
            visited.append(node_id)
            for child in sorted(adjacency[node_id]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(visited) != len(nodes):
            errors.append({"code": "graph_not_dag", "message": f"{graph_id} must be a DAG"})
        for node_id, node in nodes.items():
            for artifact in list(node.get("requires_artifacts") or []):
                text = str(artifact or "").strip()
                if text and text not in set(str(x).strip() for x in list(payload.get("artifact_vocabulary") or []) if str(x).strip()):
                    errors.append({"code": "unknown_artifact_requirement", "message": f"{graph_id}:{node_id} requires unknown artifact {text}"})
            if not str(node.get("handler_id") or "").strip():
                errors.append({"code": "missing_handler_id", "message": f"{graph_id}:{node_id} missing handler_id"})
    return {"ok": not errors, "errors": errors, "graphs": rows}


def _topological_order(graph: Mapping[str, Any]) -> List[str]:
    nodes = {str(node.get("node_id") or "").strip(): dict(node) for node in list(graph.get("nodes") or []) if str(node.get("node_id") or "").strip()}
    indegree: Dict[str, int] = {node_id: 0 for node_id in nodes}
    adjacency: Dict[str, List[str]] = {node_id: [] for node_id in nodes}
    for left, right in [tuple(edge) for edge in list(graph.get("edges") or []) if isinstance(edge, list) and len(edge) == 2]:
        adjacency[left].append(right)
        indegree[right] += 1
    queue: deque[str] = deque(sorted(node_id for node_id, count in indegree.items() if count == 0))
    order: List[str] = []
    while queue:
        node_id = queue.popleft()
        order.append(node_id)
        for child in sorted(adjacency[node_id]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) != len(nodes):
        raise GraphValidationError("graph_not_dag")
    return order


def _merge_artifact_map(target: MutableMapping[str, Any], produced: Mapping[str, Any]) -> None:
    for key, value in produced.items():
        existing = target.get(key)
        if isinstance(existing, list) and isinstance(value, list):
            merged: List[Any] = []
            for item in existing + value:
                if item not in merged:
                    merged.append(item)
            target[key] = merged
        elif isinstance(existing, dict) and isinstance(value, dict):
            merged_dict = dict(existing)
            merged_dict.update(value)
            target[key] = merged_dict
        else:
            target[key] = value


def execute_composed_graph(
    graph: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    handlers: Mapping[str, Callable[..., Dict[str, Any]]],
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    from .phase2_runtime import decide_phase2_chain_progress

    validation = validate_composed_execution_graphs(repo_root=repo_root)
    if not validation.get("ok"):
        raise GraphValidationError("composed execution graphs invalid")

    graph_id = str(graph.get("graph_id") or "").strip()
    terminal_node = str(graph.get("terminal_verifier_node") or "").strip()
    nodes = {str(node.get("node_id") or "").strip(): dict(node) for node in list(graph.get("nodes") or []) if str(node.get("node_id") or "").strip()}
    edges = [tuple(edge) for edge in list(graph.get("edges") or []) if isinstance(edge, list) and len(edge) == 2]
    adjacency: Dict[str, List[str]] = {node_id: [] for node_id in nodes}
    for left, right in edges:
        adjacency[left].append(right)
    order = _topological_order(graph)
    artifacts: Dict[str, Any] = dict(request.get("seed_artifacts") or {})
    step_records: List[Dict[str, Any]] = []
    final_outcome = "blocked"
    terminal_reason = "terminal_verifier_not_reached"

    for node_id in order:
        node = nodes[node_id]
        required_artifacts = [str(x).strip() for x in list(node.get("requires_artifacts") or []) if str(x).strip()]
        missing = [artifact for artifact in required_artifacts if artifact not in artifacts]
        if missing:
            step_records.append(
                {
                    "node_id": node_id,
                    "handler_id": str(node.get("handler_id") or ""),
                    "step_outcome": "blocked",
                    "missing_artifacts": missing,
                    "produced_artifact_keys": [],
                    "proof_gaps": ["missing_required_artifacts"],
                    "decision": "block",
                }
            )
            final_outcome = "blocked"
            terminal_reason = "missing_required_artifacts"
            break
        handler_id = str(node.get("handler_id") or "").strip()
        handler = handlers.get(handler_id)
        if handler is None:
            raise GraphValidationError(f"unknown handler {handler_id}")
        result = handler(request=request, artifacts=artifacts, repo_root=repo_root, node=node, step_records=step_records)
        produced_artifacts = dict(result.get("produced_artifacts") or {})
        _merge_artifact_map(artifacts, produced_artifacts)
        proof_gaps = [str(x).strip() for x in list(result.get("proof_gaps") or []) if str(x).strip()]
        step_outcome = str(result.get("step_outcome") or "blocked").strip() or "blocked"
        step_record = {
            "node_id": node_id,
            "handler_id": handler_id,
            "step_outcome": step_outcome,
            "proof_gaps": proof_gaps,
            "produced_artifact_keys": sorted(produced_artifacts.keys()),
            "pre_escalation_checklist": dict(result.get("pre_escalation_checklist") or {}),
            "fallback_allowed": bool(result.get("fallback_allowed")),
            "validation_commands_executed": list(result.get("validation_commands_executed") or []),
            "verifier_verdict": str(result.get("verifier_verdict") or "").strip().upper(),
        }
        if node_id == terminal_node:
            final_outcome = str(result.get("chain_outcome") or "blocked").strip() or "blocked"
            terminal_reason = str(result.get("terminal_reason") or "terminal_verifier_complete").strip() or "terminal_verifier_complete"
            step_record["decision"] = "terminal"
            step_records.append(step_record)
            break
        next_nodes = [child for child in adjacency.get(node_id, []) if child in nodes]
        resolver_allowed = any(bool(nodes[child].get("resolves_proof_gaps")) for child in next_nodes)
        decision_payload = {
            "step_outcome": step_outcome,
            "proof_gaps": proof_gaps,
            "resolver_step_allowed": resolver_allowed,
            "deterministic_recovery_remaining": int(result.get("deterministic_recovery_remaining") or 0),
            "fallback_allowed": bool(result.get("fallback_allowed")),
            "pre_escalation_checklist": dict(result.get("pre_escalation_checklist") or {}),
            "confidence_delta": float(request.get("confidence_delta") or result.get("confidence_delta") or 1.0),
            "stagnant_iterations": int(request.get("stagnant_iterations") or result.get("stagnant_iterations") or 0),
            "expected_gain": float(request.get("expected_gain") or result.get("expected_gain") or 1.0),
            "cost_per_iteration": float(request.get("cost_per_iteration") or result.get("cost_per_iteration") or 0.0),
            "retry_count": int(request.get("retry_count") or result.get("retry_count") or 0),
        }
        progress = decide_phase2_chain_progress(decision_payload, repo_root=repo_root)
        decision = str(progress.get("decision") or "block").strip() or "block"
        step_record["decision"] = decision
        step_record["checklist_complete"] = bool(progress.get("checklist_complete"))
        step_record["economic_stop_reasons"] = list(progress.get("economic_stop_reasons") or [])
        step_records.append(step_record)
        if decision in {"continue", "continue_with_proof_gap_resolver"}:
            continue
        if decision == "economic_stop":
            final_outcome = "failed"
            terminal_reason = "economic_stop"
        elif decision == "escalate":
            final_outcome = "escalated_and_blocked"
            terminal_reason = "escalation_required"
        elif decision == "retry_deterministic":
            final_outcome = "failed"
            terminal_reason = "deterministic_retry_required"
        elif decision == "stop_proof_gap":
            final_outcome = "completed_with_proof_gap"
            terminal_reason = "proof_gap_unresolved"
        else:
            final_outcome = "blocked"
            terminal_reason = "step_blocked"
        break

    return {
        "graph_id": graph_id,
        "playbook_id": str(graph.get("playbook_id") or "").strip(),
        "step_records": step_records,
        "final_outcome": final_outcome,
        "terminal_reason": terminal_reason,
        "artifacts": artifacts,
    }
