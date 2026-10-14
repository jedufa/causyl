from __future__ import annotations

from pathlib import Path

from tools.benchmark import promptset_reference_builder as builder


REPO_ROOT = Path(__file__).resolve().parents[2]


def _reference_doc_paths(record: dict) -> list[str]:
    notes = list(dict(record.get("confidence") or {}).get("notes") or [])
    docs_note = next(note for note in notes if str(note).startswith("docs_context="))
    return [path for path in docs_note.removeprefix("docs_context=").split(",") if path]


def test_promptset_reference_docs_context_paths_exist() -> None:
    prompt = {
        "output_file": "scheduler_case.md",
        "prompt": "Make scheduler restart recovery and duplicate dispatch safer.",
        "rank": 1,
        "score": 20,
        "groups": ["scheduler"],
        "source_file": "notes/scheduler.md",
        "source_repo": "causyl",
    }

    record = builder.build_reference_record(prompt)
    paths = _reference_doc_paths(record)

    assert paths
    assert all((REPO_ROOT / path).exists() for path in paths)
    assert not any(path.startswith("docs/agentic-pipeline-v0.1/") for path in paths)
    assert not any(path.startswith("docs/persistent_files_chatgpt/") for path in paths)
