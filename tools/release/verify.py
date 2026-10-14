"""Run the explicit source-preview regression selection, without agent/provider calls."""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

TESTS = [
    'tests/control_plane/test_project_runner_jobs.py',
    'tests/control_plane/test_canonical_experiment_contract_regression_anchor.py',
    'tests/control_plane/test_experiment_contract.py',
    'tests/control_plane/test_experiment_run_lifecycle_truth.py',
    'tests/control_plane/test_runner_logging.py',
    'tests/control_plane/test_scheduler_live_watch.py',
    'tests/control_plane/test_scheduler_batch_from_parent.py',
    'tests/control_plane/test_continuation_packet_bootstrap.py',
    'tests/control_plane/webui/test_webui_ledger_actions.py',
    'tests/control_plane/webui/test_plane_shell_ui_contracts.py',
    'tests/control_plane/webui/test_standalone_plane_boundary.py',
    'tests/control_plane/webui/test_public_release_defaults.py',
    'tests/pipeline/test_knowledge_asset_integrity.py',
    'tests/control_plane/test_contract_source.py',
    'tests/control_plane/test_action_plane_design_gap_contracts.py',
    'tests/control_plane/test_action_plane_phase1_contracts.py',
    'tests/control_plane/test_action_plane_phase1_runtime.py',
    'tests/control_plane/test_action_plane_phase2_foundations.py',
    'tests/control_plane/test_action_plane_phase2_playbooks.py',
    'tests/control_plane/test_action_plane_phase3_foundations.py',
    'tests/control_plane/test_action_plane_phase3_operator_layers.py',
    'tests/control_plane/test_action_plane_phase4_foundations.py',
    'tests/control_plane/test_action_plane_phase4_operator_layers.py',
    'tests/control_plane/test_action_plane_real_life_use_cases.py',
    'tests/control_plane/test_plane_cli.py',
    'tests/control_plane/e2e/test_action_plane_e2e.py',
    'tests/control_plane/e2e/test_action_plane_real_life_use_cases.py',
    'tests/control_plane/webui/test_request_security.py',
    'tests/control_plane/webui/test_screenshot_export.py',
    'tests/tools/test_plane_critical_regression_pack.py',
    'tests/control_plane/webui/test_assistant_bridge.py',
    'tests/control_plane/test_scheduled_jobs.py::test_build_verify_loop_does_not_recreate_family_closeout_when_family_already_verified',
    'tests/control_plane/test_scheduled_jobs.py::test_queue_execution_family_closeout_retires_existing_job_when_family_already_verified',
    'tests/control_plane/test_scheduled_jobs.py::test_queue_execution_family_closeout_retires_existing_job_when_child_dependencies_missing',
    'tests/tools/test_intent_sheet_next.py::test_next_sheets_set_safe_only_excludes_resume_existing_family_candidates',
]

def main() -> int:
    root = Path(__file__).resolve().parents[2]
    return subprocess.call([sys.executable, '-m', 'pytest', '-q', '-o', 'addopts=', *TESTS], cwd=root)

if __name__ == '__main__':
    raise SystemExit(main())
