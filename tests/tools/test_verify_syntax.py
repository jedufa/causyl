from __future__ import annotations

from tools.scripts import verify_syntax


def test_repair_python_source_scans_back_from_indentation_error() -> None:
    source = """\
def test_joined_header() -> None:    value = 1
    assert value == 1
    assert value > 0
"""

    repaired, changed, notes = verify_syntax._repair_python_source(source, "joined_header.py")

    assert changed is True
    assert notes == ["inline_suite_split:1"]
    ok, error_text = verify_syntax._compile_python_source(repaired, "joined_header.py")
    assert ok is True, error_text
    assert "def test_joined_header() -> None:\n    value = 1\n" in repaired


def test_repair_python_source_splits_joined_assignment_line() -> None:
    source = """\
VALUE = 1
OTHER = 2    THIRD = 3
"""

    repaired, changed, notes = verify_syntax._repair_python_source(source, "joined_assignment.py")

    assert changed is True
    assert notes == ["joined_statement_split:2"]
    ok, error_text = verify_syntax._compile_python_source(repaired, "joined_assignment.py")
    assert ok is True, error_text
    assert "OTHER = 2\nTHIRD = 3\n" in repaired
