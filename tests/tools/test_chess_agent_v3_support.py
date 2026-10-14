from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from infra.benchmarks.chess.chess_agent_v3_support import BenchmarkAgentBackend


class _FakeTool:
    def __init__(self, name: str, fail_after_preview: bool = False) -> None:
        self.name = name
        self.fail_after_preview = fail_after_preview
        self.calls: list[tuple[str, Any]] = []
        self.tool_schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Fake tool {name}",
                "parameters": {
                    "type": "object",
                    "additionalProperties": True,
                },
            },
        }

    async def before_hitl(self, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("before_hitl", dict(arguments)))
        return {"preview": True, "arguments": dict(arguments)}

    async def after_hitl(
        self, arguments: dict[str, Any], preview: Any, approval: str
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "after_hitl",
                {"arguments": dict(arguments), "preview": preview, "approval": approval},
            )
        )
        if self.fail_after_preview:
            raise RuntimeError(f"{self.name} failed after approval")
        return {"approved": approval == "approved", "arguments": dict(arguments), "preview": preview}


class _SequencedFakeLLM:
    def __init__(self, plans: list[dict[str, Any]]) -> None:
        self._plans = [dict(plan) for plan in plans]
        self._index = 0

    async def structured_completion(
        self,
        messages: list[dict[str, Any]],
        response_schema: Any,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        if self._index < len(self._plans):
            plan = dict(self._plans[self._index])
            self._index += 1
            return plan
        return {
            "thought": "Stop after the scripted steps.",
            "final": True,
            "final_output": "Sequenced smoke test completed.",
            "tool_calls": [],
        }


class _MalformedToolLLM:
    async def structured_completion(
        self,
        messages: list[dict[str, Any]],
        response_schema: Any,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        return {
            "thought": "Try a malformed tool call.",
            "final": False,
            "final_output": "",
            "tool_calls": [
                {
                    "tool_name": "file_editor",
                    "arguments": "not-json",
                    "purpose": "Negative-path check",
                }
            ],
        }


def test_support_runtime_executes_required_tools_with_approval_law() -> None:
    file_editor_tool = _FakeTool("file_editor")
    python_repl_tool = _FakeTool("python_repl")
    backend = BenchmarkAgentBackend(
        llm=_SequencedFakeLLM(
            [
                {
                    "thought": "Inspect the target file first.",
                    "final": False,
                    "final_output": "",
                    "tool_calls": [
                        {
                            "tool_name": "file_editor",
                            "arguments": {"path": "main.py", "action": "read"},
                            "purpose": "Inspect workspace file",
                        }
                    ],
                },
                {
                    "thought": "Run a small syntax check next.",
                    "final": False,
                    "final_output": "",
                    "tool_calls": [
                        {
                            "tool_name": "python_repl",
                            "arguments": {"code": "print('ok')"},
                            "purpose": "Verify a Python path",
                        }
                    ],
                },
                {
                    "thought": "The smoke test hit both required tools.",
                    "final": True,
                    "final_output": "Smoke test completed.",
                    "tool_calls": [],
                },
            ]
        ),
        tools={"file_editor": file_editor_tool, "python_repl": python_repl_tool},
        system_prompt="You are a coding agent.",
        max_turns=4,
        messages=[],
    )

    result = asyncio.run(backend.run("Inspect the workspace and stop."))

    assert result == "Smoke test completed."
    assert backend._messages[0]["role"] == "system"
    assert backend._messages[1]["role"] == "user"
    assert any(message.get("role") == "tool" for message in backend._messages)
    assert file_editor_tool.calls == [
        ("before_hitl", {"path": "main.py", "action": "read"}),
        (
            "after_hitl",
            {
                "arguments": {"path": "main.py", "action": "read"},
                "preview": {"preview": True, "arguments": {"path": "main.py", "action": "read"}},
                "approval": "approved",
            },
        ),
    ]
    assert python_repl_tool.calls == [
        ("before_hitl", {"code": "print('ok')"}),
        (
            "after_hitl",
            {
                "arguments": {"code": "print('ok')"},
                "preview": {"preview": True, "arguments": {"code": "print('ok')"}},
                "approval": "approved",
            },
        ),
    ]


def test_support_runtime_reports_missing_required_tool() -> None:
    backend = BenchmarkAgentBackend(
        llm=_SequencedFakeLLM([]),
        tools={"file_editor": _FakeTool("file_editor")},
        system_prompt="You are a coding agent.",
        max_turns=2,
        messages=[],
    )

    result = asyncio.run(backend.run("Run a check."))

    assert "Missing required tools: python_repl." == result
    assert backend._messages[-1]["content"] == result


def test_support_runtime_records_tool_failure_trace() -> None:
    backend = BenchmarkAgentBackend(
        llm=_SequencedFakeLLM(
            [
                {
                    "thought": "Trigger a tool failure.",
                    "final": False,
                    "final_output": "",
                    "tool_calls": [
                        {
                            "tool_name": "file_editor",
                            "arguments": {"path": "main.py", "action": "read"},
                            "purpose": "Negative-path check",
                        }
                    ],
                },
                {
                    "thought": "Stop honestly after the failure.",
                    "final": True,
                    "final_output": "Stopped after a tool failure; inspect agent._messages for the error trace.",
                    "tool_calls": [],
                },
            ]
        ),
        tools={
            "file_editor": _FakeTool("file_editor", fail_after_preview=True),
            "python_repl": _FakeTool("python_repl"),
        },
        system_prompt="You are a coding agent.",
        max_turns=3,
        messages=[],
    )

    result = asyncio.run(backend.run("Trigger a tool failure."))

    assert "tool failure" in result.lower()
    assert any(
        message.get("role") == "tool" and "RuntimeError" in message.get("content", "")
        for message in backend._messages
    )


def test_support_runtime_records_unknown_tool_and_budget_stop() -> None:
    backend = BenchmarkAgentBackend(
        llm=_SequencedFakeLLM(
            [
                {
                    "thought": "Try a non-existent tool.",
                    "final": False,
                    "final_output": "",
                    "tool_calls": [
                        {
                            "tool_name": "shell_exec",
                            "arguments": {"command": "ls"},
                            "purpose": "Negative-path check",
                        }
                    ],
                }
            ]
        ),
        tools={"file_editor": _FakeTool("file_editor"), "python_repl": _FakeTool("python_repl")},
        system_prompt="You are a coding agent.",
        max_turns=1,
        messages=[],
    )

    result = asyncio.run(backend.run("Use a tool that does not exist."))

    assert "turn budget" in result.lower()
    assert any(
        message.get("role") == "tool"
        and message.get("name") == "shell_exec"
        and "unknown tool" in message.get("content", "")
        for message in backend._messages
    )


def test_support_runtime_stops_on_malformed_tool_calls() -> None:
    backend = BenchmarkAgentBackend(
        llm=_MalformedToolLLM(),
        tools={"file_editor": _FakeTool("file_editor"), "python_repl": _FakeTool("python_repl")},
        system_prompt="You are a coding agent.",
        max_turns=1,
        messages=[],
    )

    result = asyncio.run(backend.run("Emit a malformed tool call."))

    assert "malformed tool calls" in result.lower()
    assert any(
        message.get("role") == "tool"
        and message.get("name") == "planner_validation"
        and "arguments must decode to an object" in message.get("content", "")
        for message in backend._messages
    )


def test_support_package_imports_remain_portable() -> None:
    support_root = Path("infra/benchmarks/chess/chess_agent_v3_support")
    stdlib_modules = set(getattr(sys, "stdlib_module_names", ()))
    local_module_names = {path.stem for path in support_root.glob("*.py")}

    imported_roots: set[str] = set()
    for source_path in support_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_roots.add(alias.name.split(".", 1)[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

    disallowed = sorted(
        name for name in imported_roots if name not in stdlib_modules and name not in local_module_names
    )
    assert disallowed == []


def test_support_runtime_message_payload_contains_traceable_json() -> None:
    backend = BenchmarkAgentBackend(
        llm=_SequencedFakeLLM(
            [
                {
                    "thought": "Inspect the target file first.",
                    "final": False,
                    "final_output": "",
                    "tool_calls": [
                        {
                            "tool_name": "file_editor",
                            "arguments": {"path": "main.py", "action": "read"},
                            "purpose": "Inspect workspace file",
                        }
                    ],
                },
                {
                    "thought": "Done.",
                    "final": True,
                    "final_output": "done",
                    "tool_calls": [],
                },
            ]
        ),
        tools={"file_editor": _FakeTool("file_editor"), "python_repl": _FakeTool("python_repl")},
        system_prompt="system",
        max_turns=2,
        messages=[],
    )

    asyncio.run(backend.run("trace"))

    tool_payloads = [
        json.loads(message["content"])
        for message in backend._messages
        if message.get("role") == "tool" and message.get("name") == "file_editor"
    ]
    assert tool_payloads[0]["purpose"] == "Inspect workspace file"
    assert tool_payloads[0]["result"]["approved"] is True
