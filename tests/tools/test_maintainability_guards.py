from __future__ import annotations

import filecmp
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.splitlines()


def _tracked_file_modes() -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    modes: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) == 4:
            modes[parts[3]] = parts[0]
    return modes


def _line_count(relpath: str) -> int:
    return len((REPO_ROOT / relpath).read_text(encoding="utf-8").splitlines())


def test_core_entrypoints_stay_small() -> None:
    guarded = {
        "infra/webui/server_routes_and_main.py": 20,
        "infra/webui/server_agents.py": 20,
        "infra/webui/server_lab_assistant_task_register.py": 20,
        "infra/controlplane/scheduled_jobs.py": 20,
        "tools/shared/fsio.py": 120,
        "infra/webui/server_route_dispatch.py": 140,
        "infra/webui/server_agents_parsing.py": 120,
        "infra/controlplane/scheduled_jobs_parsing.py": 120,
    }
    for relpath, max_lines in guarded.items():
        assert _line_count(relpath) <= max_lines, relpath


def test_split_directories_keep_expected_part_counts() -> None:
    expected = {
        "infra/webui/server_routes_and_main_parts": 6,
        "infra/webui/server_agents_parts": 8,
        "infra/webui/server_lab_assistant_task_register_parts": 9,
        "infra/controlplane/scheduled_jobs_parts": 10,
    }
    for relpath, count in expected.items():
        files = [p for p in (REPO_ROOT / relpath).iterdir() if p.is_file() and p.suffix == ".py"]
        assert len(files) == count, relpath


def test_repo_root_keeps_code_and_generated_outputs_out_of_top_level() -> None:
    tracked = _tracked_files()

    allowed_top_level_entries = {
        ".gitattributes",
        ".gitignore",
        "AGENTS.md",
        "README.md",
        "agents.py",
        "config",
        "developer",
        "docker-compose.yml",
        "docs",
        "experiments",
        "experiments_shared.py",
        "governance",
        "infra",
        "mkdocs.yml",
        "package-lock.json",
        "package.json",
        "pyproject.toml",
        "pytest.ini",
        "requirements.txt",
        "tests",
        "tools",
    }
    tracked_top_level_entries = {path.split("/", 1)[0] for path in tracked}
    assert tracked_top_level_entries == allowed_top_level_entries

    root_python_modules = sorted(path for path in tracked if "/" not in path and path.endswith(".py"))
    assert root_python_modules == ["agents.py", "experiments_shared.py"]

    root_local_or_generated_outputs = [
        ".projflow_recents.json",
        "blood-work-all.csv",
        "cloudflared-linux-amd64.deb",
        "config/pipeline.local.override.yaml",
        "verification_workflow_run_output.json",
    ]
    assert not sorted(set(tracked).intersection(root_local_or_generated_outputs))

    generated_or_local_roots = (
        ".pipeline_workspaces/",
        ".tooling/",
        "archives/",
        "artifacts/",
        "benchmarks/",
        "bundle/",
        "evidence/",
        "node_modules/",
        "agents/",
        "experiments_shared/",
        "mobile/",
        "runtime/",
        "site/",
        "tmp/",
        "tmp_exports/",
    )
    tracked_generated_or_local_roots = [
        path for path in tracked if path.startswith(generated_or_local_roots)
    ]
    assert tracked_generated_or_local_roots == []

    root_archive_or_installer_files = [
        path
        for path in tracked
        if "/" not in path and path.endswith((".bin", ".deb", ".pt", ".pth", ".tar.gz", ".tgz", ".zip"))
    ]
    assert root_archive_or_installer_files == []

    tracked_backup_copies = [path for path in tracked if path.endswith(" - Copy.py")]
    assert tracked_backup_copies == []

    generated_emergence_review_runs = [
        path for path in tracked if path.startswith("experiments/projects/emergence/_artifacts/review_runs_")
    ]
    assert generated_emergence_review_runs == []

    tracked_tools_archives = [path for path in tracked if path.startswith("tools/") and path.endswith(".zip")]
    assert tracked_tools_archives == []

    legacy_top_level_agentic_docs = [
        path for path in tracked if path.startswith("docs/agentic-pipeline-v0.1/")
    ]
    assert legacy_top_level_agentic_docs == []

    legacy_server_interface = [path for path in tracked if path.startswith("_server-interface/")]
    assert legacy_server_interface == []

    legacy_agent_tree = [path for path in tracked if path.startswith("agents/")]
    assert legacy_agent_tree == []

    legacy_experiments_shared_tree = [
        path for path in tracked if path.startswith("experiments_shared/")
    ]
    assert legacy_experiments_shared_tree == []

    legacy_evidence_tree = [path for path in tracked if path.startswith("evidence/")]
    assert legacy_evidence_tree == []

    legacy_pipeline_benchmark_tree = [
        path for path in tracked if path.startswith("tools/benchmark/") and path != "tools/benchmark/__init__.py"
    ]
    assert legacy_pipeline_benchmark_tree == []

    legacy_mobile_tree = [path for path in tracked if path.startswith("mobile/")]
    assert legacy_mobile_tree == []


def test_active_files_do_not_reference_untracked_context_sources() -> None:
    active_prefixes = ("config/", "experiments/shared/", "governance/agents/", "infra/", "tools/")
    active_root_files = ("AGENTS.md", "README.md", "agents.py", "experiments_shared.py", "mkdocs.yml")
    legacy_ref = "docs/persistent_files_chatgpt/"
    offenders: list[str] = []

    for relpath in _tracked_files():
        if relpath.startswith(("governance/ledger/", "infra/pipeline/knowledge_drafts/")):
            continue
        is_active_surface = (
            relpath in active_root_files
            or relpath.startswith(active_prefixes)
            or (relpath.startswith("governance/") and relpath.endswith(".json"))
        )
        if not is_active_surface:
            continue
        path = REPO_ROOT / relpath
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if legacy_ref in text:
            offenders.append(relpath)

    assert offenders == []


def test_tracked_docs_and_data_are_not_executable() -> None:
    modes = _tracked_file_modes()
    data_suffixes = (".csv", ".html", ".json", ".md", ".pdf", ".png", ".toml", ".txt", ".yaml", ".yml", ".zip")
    executable_docs_or_data = [
        path for path, mode in modes.items() if mode == "100755" and path.endswith(data_suffixes)
    ]
    assert executable_docs_or_data == []


def test_tracked_package_initializers_are_not_executable() -> None:
    modes = _tracked_file_modes()
    executable_initializers = [
        path for path, mode in modes.items() if mode == "100755" and path.endswith("/__init__.py")
    ]
    assert executable_initializers == []


def test_embedded_emergence_tool_bin_mirror_stays_identical() -> None:
    root_tools = REPO_ROOT / "tools" / "bin"
    embedded_tools = REPO_ROOT / "experiments" / "projects" / "emergence" / "tools" / "bin"
    mirrored_names = sorted(
        {path.name for path in root_tools.iterdir() if path.is_file()}
        & {path.name for path in embedded_tools.iterdir() if path.is_file()}
    )

    drifted = [
        name
        for name in mirrored_names
        if not filecmp.cmp(root_tools / name, embedded_tools / name, shallow=False)
    ]

    assert drifted == []
