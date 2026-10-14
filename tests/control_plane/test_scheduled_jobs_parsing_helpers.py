from __future__ import annotations

from infra.controlplane import scheduled_jobs_parsing as parsing


def test_extract_markdown_section_and_bullets() -> None:
    text = """
## Changed Files
- infra/webui/server.py
- infra/controlplane/scheduled_jobs.py

## Notes
- later
""".strip()

    assert "infra/webui/server.py" in parsing.extract_markdown_section(text, "Changed Files")
    assert parsing.extract_named_bullet_block(text, "Changed Files") == [
        "infra/webui/server.py",
        "infra/controlplane/scheduled_jobs.py",
    ]


def test_has_named_heading_normalizes_heading_variants() -> None:
    text = """
**Validation Commands Run**
- pytest tests/control_plane/test_scheduled_jobs.py
""".strip()

    assert parsing.has_named_heading(text, "validation commands run")
