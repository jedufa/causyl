from __future__ import annotations

import asyncio
import inspect

from infra.benchmarks.chess import chess_agent_v3


class _FakeBackend:
    def __init__(self, messages):
        self._messages = messages

    async def run(self, task_prompt: str) -> str:
        self._messages.append({"role": "assistant", "content": f"handled {task_prompt}"})
        return "handled task"


def test_your_agent_preserves_public_constructor_surface() -> None:
    llm = object()
    tools = {"file_editor": object(), "python_repl": object()}

    agent = chess_agent_v3.YourAgent(
        llm=llm,
        tools=tools,
        system_prompt="system prompt",
        max_turns=7,
    )

    assert agent.llm is llm
    assert agent.tools is tools
    assert agent.system_prompt == "system prompt"
    assert agent.max_turns == 7
    assert agent._messages == []
    assert inspect.iscoroutinefunction(agent.run)


def test_run_delegates_to_support_backend_and_exposes_messages(monkeypatch) -> None:
    calls = {}

    def _fake_load_support_builder():
        def _builder(**kwargs):
            calls.update(kwargs)
            return _FakeBackend(kwargs["messages"])

        return _builder

    monkeypatch.setattr(chess_agent_v3, "_load_support_builder", _fake_load_support_builder)

    agent = chess_agent_v3.YourAgent(
        llm="fake-llm",
        tools={"file_editor": object()},
        system_prompt="bench system",
        max_turns=3,
    )

    result = asyncio.run(agent.run("solve the task"))

    assert result == "handled task"
    assert calls["llm"] == "fake-llm"
    assert calls["system_prompt"] == "bench system"
    assert calls["max_turns"] == 3
    assert agent._messages == [{"role": "assistant", "content": "handled solve the task"}]


def test_run_reports_missing_support_runtime_in_messages(monkeypatch) -> None:
    def _raise_missing_support():
        raise chess_agent_v3.SupportRuntimeUnavailable("missing support package")

    monkeypatch.setattr(chess_agent_v3, "_load_support_builder", _raise_missing_support)

    agent = chess_agent_v3.YourAgent(
        llm=object(),
        tools={"file_editor": object()},
        system_prompt="bench system",
    )

    result = asyncio.run(agent.run("solve the task"))

    assert result == "V3 support runtime unavailable: missing support package"
    assert agent._messages == [
        {"role": "system", "content": "bench system"},
        {"role": "user", "content": "solve the task"},
        {
            "role": "assistant",
            "content": "V3 support runtime unavailable: missing support package",
        },
    ]
