from __future__ import annotations

from pathlib import Path

from infra.webui import server_agents
from infra.webui import server_codex_programmatic_surface as codex_surface
from infra.webui import server_lab_assistant_operator_snapshot as operator_snapshot
from infra.webui import server_lab_assistant_task_register as task_register


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_front_door_contract_documents_current_canonical_routes_and_modules() -> None:
    doc = (
        _repo_root() / "docs" / "10_operator" / "workflows" / "Control_Surface_Front_Door.html"
    ).read_text(encoding="utf-8")

    assert "<strong>UID:</strong> PIS-20260327-132825Z-D8FB" in doc
    assert "GET /api/admin/lab_assistant/snapshot" in doc
    assert "POST /api/agents/send" in doc
    assert "POST /api/admin/server/refresh_runtime" in doc
    assert "infra/webui/server_lab_assistant_operator_snapshot.py" in doc
    assert "infra/webui/server_agents.py" in doc
    assert "infra/webui/server_lab_assistant_intake.py" in doc
    assert "infra/webui/server_codex_programmatic_surface.py" in doc
    assert "infra/webui/server_lab_assistant_task_register.py" in doc


def test_front_door_contract_points_to_existing_canonical_implementations() -> None:
    assert operator_snapshot.__doc__
    assert "read-only projection" in (operator_snapshot.__doc__ or "")
    assert hasattr(server_agents, "send_message")
    assert callable(server_agents.send_message)
    assert "Codex-managed durable work" in (codex_surface.__doc__ or "")
    assert hasattr(task_register, "record_task_run_intervention")
    assert callable(task_register.record_task_run_intervention)


def test_user_shims_are_explicitly_labeled_as_compatibility_wrappers() -> None:
    repo = _repo_root()
    readme = (repo / "tools" / "user" / "README.md").read_text(encoding="utf-8")
    server = (repo / "tools" / "user" / "server").read_text(encoding="utf-8")
    scheduler = (repo / "tools" / "user" / "scheduler").read_text(encoding="utf-8")
    pis = (repo / "tools" / "user" / "pis").read_text(encoding="utf-8")

    assert "compatibility wrappers" in readme
    assert "docs/10_operator/workflows/Control_Surface_Front_Door.html" in readme
    assert "Compatibility wrapper only." in server
    assert "/api/admin/lab_assistant/snapshot" in server
    assert "Compatibility wrapper only." in scheduler
    assert "/api/admin/lab_assistant/snapshot" in scheduler
    assert "Compatibility launcher for PIS-oriented ledger helpers." in pis
