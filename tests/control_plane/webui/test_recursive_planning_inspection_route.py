from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from infra.controlplane import recursive_dry_run_planner
from infra.controlplane import recursive_planning_nonlive_admission
from infra.webui import server_routes_and_main as routes


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str) -> None:
        self.path = path
        self.headers = {"Content-Length": "0"}
        self.rfile = io.BytesIO()
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _json_body(req: _DummyReq) -> dict:
    raw = req.wfile.getvalue().decode("utf-8")
    start = raw.find("{")
    assert start >= 0
    return json.loads(raw[start:])


def _base_request() -> dict:
    return {
        "schema_version": "recursive_dry_run_plan_request_v1",
        "plan_id": "plan_recursive_demo_001",
        "root_plan_id": "plan_recursive_demo_001",
        "parent_plan_id": "",
        "parent_job_id": "job_plan_root",
        "planning_mode": "dry_run_only",
        "root_binding": {"binding_type": "ledger_uid", "binding_id": "PIS-20260409-153903Z-7C65"},
        "plan_truth_refs": {
            "ledger_uids": ["PIS-plan_root"],
            "campaign_ids": ["camp_plan_root"],
            "job_ids": ["job_plan_root"],
            "run_ids": [],
            "receipt_refs": ["runtime/state/scheduled_jobs/receipts/plan_root.json"],
            "continuation_packet_ids": ["pkt_plan_root"],
            "artifact_refs": ["docs/contracts/plan_root.md"],
        },
        "plan_event_refs": ["events.jsonl#plan_root"],
        "root_node": {
            "node_id": "node_root",
            "job_shape": "campaign_job",
            "truth_refs": {
                "ledger_uids": ["PIS-plan_root"],
                "campaign_ids": ["camp_plan_root"],
                "job_ids": ["job_plan_root"],
                "run_ids": [],
                "receipt_refs": ["runtime/state/scheduled_jobs/receipts/plan_root.json"],
                "continuation_packet_ids": ["pkt_plan_root"],
                "artifact_refs": ["docs/contracts/plan_root.md"],
            },
            "node_event_refs": ["events.jsonl#node_root"],
            "dependency_node_ids": [],
            "budget_balance": {
                "job_uid": "job_plan_root",
                "unit": "usd_cents",
                "allocated_minor_units": 1100,
                "cost_debit_minor_units": 100,
                "cost_credit_minor_units": 0,
                "remaining_minor_units": 1000,
            },
            "campaign_phase_category": "planning",
            "descendants": [
                {
                    "node_id": "node_child_a",
                    "job_shape": "verification_loop_job",
                    "truth_refs": {
                        "ledger_uids": ["PIS-node_child_a"],
                        "campaign_ids": ["camp_plan_root"],
                        "job_ids": ["job_node_child_a"],
                        "run_ids": [],
                        "receipt_refs": ["runtime/state/scheduled_jobs/receipts/node_child_a.json"],
                        "continuation_packet_ids": ["pkt_node_child_a"],
                        "artifact_refs": ["docs/contracts/node_child_a.md"],
                    },
                    "node_event_refs": ["events.jsonl#node_child_a"],
                    "dependency_node_ids": [],
                    "requested_budget_minor_units": 250,
                    "parent_reserved_budget_minor_units": 100,
                    "minimum_parent_remaining_minor_units": 100,
                    "descendants": [],
                }
            ],
        },
    }


def _campaign_entrypoint() -> dict:
    return {
        "entrypoint_kind": "campaign_phase",
        "parent_job_shape": "campaign_job",
        "parent_job_id": "job_plan_root",
        "binding_type": "ledger_uid",
        "binding_id": "PIS-20260409-153903Z-7C65",
        "campaign_id": "camp_plan_root",
        "phase_id": "phase_recursive_plan",
        "campaign_phase_category": "planning",
        "campaign_truth_refs": ["campaigns/camp_plan_root.json"],
    }


def test_recursive_planning_inspection_route_lists_and_reads_durable_projection(tmp_path: Path, monkeypatch) -> None:
    plan_result = recursive_dry_run_planner.build_recursive_dry_run_plan(_base_request(), repo_root=tmp_path)
    recursive_planning_nonlive_admission.materialize_recursive_planning_nonlive_admission(
        entrypoint=_campaign_entrypoint(),
        plan_result=plan_result,
        repo_root=tmp_path,
    )

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_admin_guard", lambda *_args, **_kwargs: True)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    list_req = _DummyReq(cfg=cfg, path="/api/admin/recursive_planning/inspection?binding_type=ledger_uid&binding_id=PIS-20260409-153903Z-7C65")
    routes.WebUIRequestHandler.do_GET(list_req)  # type: ignore[arg-type]
    list_payload = _json_body(list_req)

    assert list_req._status == 200
    assert list_payload["schema_version"] == "recursive_planning_inspection_list_v1"
    assert list_payload["total_plans"] == 1
    assert list_payload["projections"][0]["status"] == "non_live_admitted"

    detail_req = _DummyReq(cfg=cfg, path="/api/admin/recursive_planning/inspection?plan_id=plan_recursive_demo_001")
    routes.WebUIRequestHandler.do_GET(detail_req)  # type: ignore[arg-type]
    detail_payload = _json_body(detail_req)

    assert detail_req._status == 200
    assert detail_payload["projection"]["planned_graph"]["node_count"] == 2
    assert detail_payload["projection"]["non_live_admission"]["campaign_context"]["phase_id"] == "phase_recursive_plan"
