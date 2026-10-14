from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from infra.controlplane import recursive_planning_family_proof as proof
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


def _bound_proof_artifact() -> Path:
    base = Path("governance") / "ledger" / "patch_instruction_sheets" / "causyl"
    rel = (
        Path("2026")
        / "04"
        / "PIS-20260409-154917Z-E01A__prove-recursive-job-graph-planning-policy-end-to-end"
        / "artifacts"
        / "verification"
        / "recursive_planning_family_proof_audit.md"
    )
    for ledger_status in ("active", "verified"):
        candidate = base / ledger_status / rel
        if candidate.exists():
            return candidate
    return base / "active" / rel


def test_recursive_planning_family_proof_covers_happy_path_negative_paths_and_durable_listing(tmp_path: Path) -> None:
    report = proof.collect_recursive_planning_family_proof(repo_root=tmp_path)

    assert report["schema_version"] == "recursive_planning_family_proof_v1"
    assert report["uid"] == "PIS-20260409-154917Z-E01A"
    assert report["case_count"] == 5
    assert report["all_cases_met"] is True
    assert report["listing_total_plans"] == 4

    happy = report["cases"]["happy_path_non_live_admitted"]
    assert happy["planner_ok"] is True
    assert happy["admission_ok"] is True
    assert happy["projection_status"] == "non_live_admitted"
    assert happy["phase_id"] == "phase_recursive_plan"
    assert happy["node_count"] == 3
    assert happy["dependency_edge_count"] == 3
    assert happy["blocking_reason_codes"] == ["recursive_planning_dry_run_only"]
    assert len(happy["receipt_paths"]) == 4
    assert any(path.endswith("compiled_work_order_envelope.json") for path in happy["receipt_paths"])

    cycle = report["cases"]["dependency_cycle_rejected"]
    assert cycle["planner_ok"] is False
    assert cycle["projection_status"] == "blocked"
    assert "dependency_cycle_detected" in cycle["blocking_reason_codes"]

    depth = report["cases"]["depth_cap_rejected"]
    assert depth["planner_ok"] is False
    assert depth["projection_status"] == "blocked"
    assert "depth_cap_exceeded" in depth["blocking_reason_codes"]

    budget = report["cases"]["budget_stop_rejected"]
    assert budget["planner_ok"] is False
    assert budget["projection_status"] == "blocked"
    assert "child_budget_exceeds_spendable" in budget["blocking_reason_codes"]

    live_launch = report["cases"]["live_launch_forbidden"]
    assert live_launch["admission_ok"] is False
    assert "live_recursive_launch_forbidden" in live_launch["error_codes"]
    assert "live_recursive_launch_forbidden" in live_launch["non_launch_reason_codes"]

    assert report["listing_statuses"] == {
        "plan_recursive_budget_001": "blocked",
        "plan_recursive_cycle_001": "blocked",
        "plan_recursive_depth_001": "blocked",
        "plan_recursive_happy_001": "non_live_admitted",
    }


def test_recursive_planning_family_proof_surfaces_happy_path_projection_through_the_plane_inspection_route(
    tmp_path: Path, monkeypatch
) -> None:
    proof.collect_recursive_planning_family_proof(repo_root=tmp_path)

    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    monkeypatch.setattr(routes, "_admin_guard", lambda *_args, **_kwargs: True)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    list_req = _DummyReq(
        cfg=cfg,
        path="/api/admin/recursive_planning/inspection?binding_type=ledger_uid&binding_id=PIS-20260409-153903Z-7C65",
    )
    routes.WebUIRequestHandler.do_GET(list_req)  # type: ignore[arg-type]
    list_payload = _json_body(list_req)

    assert list_req._status == 200
    assert list_payload["schema_version"] == "recursive_planning_inspection_list_v1"
    assert list_payload["total_plans"] == 4
    projected = {row["plan_id"]: row["status"] for row in list_payload["projections"]}
    assert projected["plan_recursive_happy_001"] == "non_live_admitted"
    assert projected["plan_recursive_cycle_001"] == "blocked"

    detail_req = _DummyReq(cfg=cfg, path="/api/admin/recursive_planning/inspection?plan_id=plan_recursive_happy_001")
    routes.WebUIRequestHandler.do_GET(detail_req)  # type: ignore[arg-type]
    detail_payload = _json_body(detail_req)

    assert detail_req._status == 200
    assert detail_payload["schema_version"] == "recursive_planning_inspection_view_v1"
    assert detail_payload["projection"]["planned_graph"]["node_count"] == 3
    assert detail_payload["projection"]["non_live_admission"]["campaign_context"]["phase_id"] == "phase_recursive_plan"
    assert detail_payload["projection"]["blocking_reason_codes"] == ["recursive_planning_dry_run_only"]


def test_bound_recursive_planning_proof_artifact_lists_the_family_cases_and_constraints() -> None:
    artifact = _bound_proof_artifact()
    assert artifact.exists()
    body = artifact.read_text(encoding="utf-8")

    assert "PIS-20260409-154917Z-E01A" in body
    assert "happy_path_non_live_admitted" in body
    assert "dependency_cycle_rejected" in body
    assert "depth_cap_rejected" in body
    assert "budget_stop_rejected" in body
    assert "live_launch_forbidden" in body
    assert "/api/admin/recursive_planning/inspection" in body
    assert "specialized verifier review is still required" in body
