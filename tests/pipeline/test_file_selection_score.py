from tools.benchmark.file_selection_score import _canonical_equivalent_paths, _recall, _set


def test_file_selection_score_normalizes_tools_and_infra_webui_paths() -> None:
    expected = _set(["tools/webui/server_agents_orchestrator.py"])
    observed = _set(["infra/webui/server_agents_orchestrator.py"])
    assert "tools/webui/server_agents_orchestrator.py" in expected
    assert "infra/webui/server_agents_orchestrator.py" in expected
    assert "tools/webui/server_agents_orchestrator.py" in observed
    assert "infra/webui/server_agents_orchestrator.py" in observed
    assert _recall(expected, observed) == 1.0


def test_file_selection_score_normalizes_tools_and_infra_controlplane_paths() -> None:
    expected = _set(["tools/controlplane/backend_api_boundary.py"])
    observed = _set(["infra/controlplane/backend_api_boundary.py"])
    assert "tools/controlplane/backend_api_boundary.py" in expected
    assert "infra/controlplane/backend_api_boundary.py" in expected
    assert _recall(expected, observed) == 1.0


def test_file_selection_score_maps_legacy_server_routes_entrypoint_to_current_handler() -> None:
    expected = _set(["tools/webui/server_routes_and_main.py"])
    observed = _set(["infra/webui/server_routes_and_main_parts/part05__webuirequesthandler.py"])
    assert "infra/webui/server_routes_and_main_parts/part05__webuirequesthandler.py" in expected
    assert "tools/webui/server_routes_and_main.py" in observed
    assert _recall(expected, observed) == 1.0
    assert "infra/webui/server_routes_and_main_parts/part05__webuirequesthandler.py" in _canonical_equivalent_paths(
        "tools/webui/server_routes_and_main.py"
    )
