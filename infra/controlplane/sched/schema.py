from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Tuple


SCHEMA_VERSION = "sched_v2_runtime_v1"
SUPPORTED_WORKFLOW_KIND = "intent_family_v2"

GATE_CONTINUE = "continue"
GATE_COMPLETE = "complete"
GATE_CLOSE_AND_PROMOTE = "close_and_promote"


@dataclass(frozen=True)
class V2Job:
    job_id: str
    dispatch_job_id: str
    child_uid: str
    node_kind: str
    stage: str
    title: str
    depends_on_uids: Tuple[str, ...] = ()
    depends_on_job_ids: Tuple[str, ...] = ()
    projection_job_ids: Tuple[str, ...] = ()
    gate: str = GATE_CONTINUE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdmissionDecision:
    child_uid: str
    job_id: str
    dispatch_job_id: str
    decision: str
    reason: str
    gate: str
    unmet_dependencies: Tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SchedulerPlan:
    schema_version: str
    parent_uid: str
    source_intent_uid: str
    workflow_kind: str
    compilation_fingerprint: str
    jobs: Tuple[V2Job, ...]
    raw_compilation: Mapping[str, Any] = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["jobs"] = [job.to_dict() for job in self.jobs]
        payload["raw_compilation"] = dict(self.raw_compilation)
        return payload


@dataclass(frozen=True)
class SchedulerEvaluation:
    schema_version: str
    parent_uid: str
    mode: str
    family_status: str
    current_stage: str
    admitted: Tuple[AdmissionDecision, ...]
    projection: Mapping[str, Any]
    continuity: Mapping[str, Any]
    shadow_report: Mapping[str, Any]
    mutates_canonical_state: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["admitted"] = [row.to_dict() for row in self.admitted]
        payload["projection"] = dict(self.projection)
        payload["continuity"] = dict(self.continuity)
        payload["shadow_report"] = dict(self.shadow_report)
        return payload


def gate_for_node_kind(node_kind: str) -> str:
    if node_kind == "intent_family_closeout":
        return GATE_CLOSE_AND_PROMOTE
    if node_kind == "intent_validation_suite":
        return GATE_COMPLETE
    return GATE_CONTINUE
