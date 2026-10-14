"""Canonical intake, task, open-loop, and snapshot status helpers.

This module centralizes the shared status vocabulary used across intake,
task-register truth, and operator-facing projection so clarification-needed
work does not silently collapse into generic blocked state.
"""

from __future__ import annotations

from typing import Any, Dict

INTAKE_READINESS_CLARIFICATION_NEEDED = "clarification_needed"
TASK_STATUS_BLOCKED = "blocked"
TASK_STATUS_WAITING_ON_OPERATOR = "waiting_on_operator"
TASK_STATUS_NEEDS_INPUT = "needs_input"
OPEN_LOOP_WAITING_ON_OPERATOR = "waiting_on_operator"
OPEN_LOOP_BLOCKED_QUESTION = "blocked_question"

_OPERATOR_INPUT_TASK_STATUSES = {
    TASK_STATUS_WAITING_ON_OPERATOR,
    TASK_STATUS_NEEDS_INPUT,
}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def task_intake_readiness(task: Dict[str, Any]) -> str:
    intake = task.get("intake") if isinstance(task.get("intake"), dict) else {}
    return _clean_text((intake or {}).get("readiness")).lower()


def task_status(task: Dict[str, Any]) -> str:
    return _clean_text(task.get("status")).lower()


def open_loop_kind(loop: Dict[str, Any]) -> str:
    return _clean_text(loop.get("kind")).lower()


def task_waiting_on_operator(task: Dict[str, Any]) -> bool:
    status = task_status(task)
    if status in _OPERATOR_INPUT_TASK_STATUSES:
        return True
    readiness = task_intake_readiness(task)
    return readiness == INTAKE_READINESS_CLARIFICATION_NEEDED


def task_blocked_for_non_operator_reason(task: Dict[str, Any]) -> bool:
    return task_status(task) == TASK_STATUS_BLOCKED and not task_waiting_on_operator(task)


def open_loop_waiting_on_operator(loop: Dict[str, Any]) -> bool:
    return open_loop_kind(loop) == OPEN_LOOP_WAITING_ON_OPERATOR


def open_loop_blocked_question(loop: Dict[str, Any]) -> bool:
    return open_loop_kind(loop) == OPEN_LOOP_BLOCKED_QUESTION
