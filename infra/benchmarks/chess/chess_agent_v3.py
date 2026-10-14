"""Benchmark-facing V3 agent entrypoint.

This file intentionally keeps the benchmark contract thin and portable. Runtime
behavior belongs in the sibling support package rooted under
``infra/benchmarks/chess/chess_agent_v3_support/``.

Creation provenance:
- Variant: V3
- Workflow: normal recursive decomposition workflow
- Notes: the intent was imported first, the standard workflow derived a parent
  PIS, then child PISes, then scheduled family implementation/validation work.
  This file is the benchmark-facing front end; complex support behavior is
  implemented in sibling support files.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


SUPPORT_PACKAGE_ROOT = Path(__file__).with_name("chess_agent_v3_support")
SUPPORT_INIT_PATH = SUPPORT_PACKAGE_ROOT / "__init__.py"
SUPPORT_MODULE_NAME = "benchmark_chess_agent_v3_support"


class SupportRuntimeUnavailable(RuntimeError):
    """Raised when the V3 support package is missing or violates the contract."""


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _load_support_builder() -> Callable[..., Any]:
    if not SUPPORT_INIT_PATH.exists():
        raise SupportRuntimeUnavailable(
            "expected support package at infra/benchmarks/chess/chess_agent_v3_support/__init__.py"
        )

    spec = importlib.util.spec_from_file_location(
        SUPPORT_MODULE_NAME,
        SUPPORT_INIT_PATH,
        submodule_search_locations=[str(SUPPORT_PACKAGE_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise SupportRuntimeUnavailable(
            f"could not create import spec for {SUPPORT_INIT_PATH}"
        )

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    builder = getattr(module, "build_agent_backend", None)
    if not callable(builder):
        raise SupportRuntimeUnavailable(
            "support package must export callable build_agent_backend(...)"
        )
    return builder


class YourAgent:
    """Benchmark-facing wrapper for the V3 agent family."""

    def __init__(self, llm: Any, tools: Any, system_prompt: str, max_turns: int = 12) -> None:
        self.llm = llm
        self.tools = tools
        self.system_prompt = str(system_prompt or "")
        self.max_turns = max(1, int(max_turns or 12))
        self._messages: List[Dict[str, Any]] = []
        self._backend: Optional[Any] = None

    async def run(self, task_prompt: str) -> str:
        prompt_text = str(task_prompt or "")
        try:
            backend = self._ensure_backend()
        except SupportRuntimeUnavailable as exc:
            return self._fail_without_backend(prompt_text, exc)

        result = await _maybe_await(backend.run(prompt_text))
        self._sync_messages_from_backend()
        return str(result)

    def _ensure_backend(self) -> Any:
        if self._backend is not None:
            return self._backend

        builder = _load_support_builder()
        backend = builder(
            llm=self.llm,
            tools=self.tools,
            system_prompt=self.system_prompt,
            max_turns=self.max_turns,
            messages=self._messages,
        )
        if backend is None or not callable(getattr(backend, "run", None)):
            raise SupportRuntimeUnavailable(
                "build_agent_backend(...) must return an object with callable run(...)"
            )

        backend_messages = getattr(backend, "_messages", None)
        if backend_messages is None:
            backend._messages = self._messages
        elif isinstance(backend_messages, list):
            self._messages = backend_messages
        else:
            raise SupportRuntimeUnavailable(
                "support backend must expose readable list attribute _messages"
            )

        self._backend = backend
        return backend

    def _fail_without_backend(
        self, task_prompt: str, exc: SupportRuntimeUnavailable
    ) -> str:
        self._messages = []
        self._append_message("system", self.system_prompt)
        self._append_message("user", task_prompt)
        message = f"V3 support runtime unavailable: {exc}"
        self._append_message("assistant", message)
        return message

    def _append_message(self, role: str, content: str, **extra: Any) -> None:
        message = {"role": role, "content": str(content)}
        message.update(extra)
        self._messages.append(message)

    def _sync_messages_from_backend(self) -> None:
        if self._backend is None:
            return
        backend_messages = getattr(self._backend, "_messages", None)
        if isinstance(backend_messages, list):
            self._messages = backend_messages
