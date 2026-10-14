"""Self-standing V3 runtime support package for the benchmark wrapper.

This package intentionally avoids repo-local imports so it remains portable as a
benchmark artifact. The benchmark-facing wrapper in ``chess_agent_v3.py`` loads
``build_agent_backend(...)`` from here and delegates all runtime behavior.
"""

from __future__ import annotations

import inspect
import json
import traceback
from typing import Any, Dict, List, Optional, Tuple


REQUIRED_TOOL_NAMES: Tuple[str, str] = ("file_editor", "python_repl")


PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "final": {"type": "boolean"},
        "final_output": {"type": "string"},
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string"},
                    "arguments": {"type": "object"},
                    "purpose": {"type": "string"},
                },
                "required": ["tool_name", "arguments"],
                "additionalProperties": True,
            },
        },
    },
    "required": ["thought", "final", "final_output", "tool_calls"],
    "additionalProperties": True,
}


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _trim_text(value: Any, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _parse_json_object(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("expected a JSON object")
    return parsed


class BenchmarkAgentBackend:
    """Portable benchmark runtime backing the V3 wrapper."""

    def __init__(
        self,
        *,
        llm: Any,
        tools: Any,
        system_prompt: str,
        max_turns: int = 12,
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.llm = llm
        self.system_prompt = str(system_prompt or "")
        self.max_turns = max(1, int(max_turns or 12))
        self.tools = self._normalize_tools(tools)
        self.tool_schemas = self._build_tool_schemas()
        self._messages = messages if isinstance(messages, list) else []

    async def run(self, task_prompt: str) -> str:
        self._messages.clear()
        self._append_message("system", self.system_prompt)
        self._append_message("user", str(task_prompt or ""))

        missing_tools = self._missing_required_tools()
        if missing_tools:
            message = f"Missing required tools: {', '.join(missing_tools)}."
            self._append_message("assistant", message)
            return message

        for turn_index in range(1, self.max_turns + 1):
            plan = await self._plan_next_step(turn_index)
            thought = str(plan.get("thought") or "").strip()
            final = bool(plan.get("final"))
            final_output = str(plan.get("final_output") or "").strip()
            tool_calls, malformed_tool_calls = self._normalize_tool_calls(plan.get("tool_calls"))

            assistant_payload = {
                "turn": turn_index,
                "thought": thought,
                "tool_calls": tool_calls,
                "malformed_tool_calls": malformed_tool_calls,
                "final": final,
            }
            self._append_message("assistant", _json_dumps(assistant_payload))

            for malformed_tool_call in malformed_tool_calls:
                self._append_message(
                    "tool",
                    _trim_text(_json_dumps(malformed_tool_call), 2000),
                    name="planner_validation",
                )

            if final:
                resolved_output = (
                    final_output
                    or thought
                    or "Stopped because the planner reported completion without a final summary."
                )
                self._append_message("assistant", resolved_output)
                return resolved_output

            if not tool_calls:
                if malformed_tool_calls:
                    fallback = "Stopping because the planner emitted malformed tool calls without a valid repair."
                else:
                    fallback = thought or "Stopping because no actionable tool call or final answer was produced."
                self._append_message("assistant", fallback)
                return fallback

            for tool_call in tool_calls[:3]:
                await self._execute_tool_call(tool_call)

        final_text = await self._synthesize_budget_stop()
        self._append_message("assistant", final_text)
        return final_text

    def _missing_required_tools(self) -> List[str]:
        return [name for name in REQUIRED_TOOL_NAMES if name not in self.tools]

    def _normalize_tools(self, tools: Any) -> Dict[str, Any]:
        if isinstance(tools, dict):
            return {str(name): tool for name, tool in tools.items()}
        out: Dict[str, Any] = {}
        for tool in list(tools or []):
            name = str(getattr(tool, "name", "") or getattr(tool, "__name__", "")).strip()
            if name:
                out[name] = tool
        return out

    def _build_tool_schemas(self) -> List[Dict[str, Any]]:
        schemas: List[Dict[str, Any]] = []
        for name, tool in self.tools.items():
            schema = getattr(tool, "tool_schema", None) or getattr(tool, "schema", None)
            if callable(schema):
                try:
                    schema = schema()
                except Exception:
                    schema = None
            if isinstance(schema, dict):
                schemas.append(schema)
                continue
            description = str(getattr(tool, "description", "") or f"Tool named {name}.").strip()
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "parameters": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                }
            )
        return schemas

    def _normalize_tool_calls(self, value: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        out: List[Dict[str, Any]] = []
        malformed: List[Dict[str, Any]] = []
        if not isinstance(value, list):
            return out, malformed
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                malformed.append(
                    {
                        "error": "tool call entry is not an object",
                        "index": index,
                        "raw": repr(item),
                    }
                )
                continue
            tool_name = str(
                item.get("tool_name")
                or item.get("name")
                or item.get("function", {}).get("name")
                or ""
            ).strip()
            arguments = item.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = None
            if arguments is None and isinstance(item.get("function"), dict):
                arguments = item.get("function", {}).get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = None
            if not tool_name:
                malformed.append(
                    {
                        "error": "tool call is missing a tool name",
                        "index": index,
                        "raw": item,
                    }
                )
                continue
            if not isinstance(arguments, dict):
                malformed.append(
                    {
                        "error": "tool call arguments must decode to an object",
                        "index": index,
                        "tool_name": tool_name,
                        "raw": item,
                    }
                )
                continue
            out.append(
                {
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "purpose": str(item.get("purpose") or "").strip(),
                }
            )
        return out, malformed

    def _append_message(self, role: str, content: str, **extra: Any) -> None:
        message = {"role": role, "content": str(content)}
        message.update(extra)
        self._messages.append(message)

    async def _plan_next_step(self, turn_index: int) -> Dict[str, Any]:
        planner_messages = list(self._messages)
        planner_messages.append(
            {
                "role": "system",
                "content": self._planner_prompt(turn_index),
            }
        )

        if hasattr(self.llm, "structured_completion"):
            try:
                response = await _maybe_await(
                    self.llm.structured_completion(
                        planner_messages,
                        response_schema=PLAN_SCHEMA,
                        temperature=0.0,
                    )
                )
                if isinstance(response, dict):
                    return response
                if hasattr(response, "model_dump"):
                    dumped = response.model_dump()
                    if isinstance(dumped, dict):
                        return dumped
                if hasattr(response, "dict"):
                    dumped = response.dict()
                    if isinstance(dumped, dict):
                        return dumped
            except Exception as exc:
                self._append_message("tool", f"planner structured fallback: {exc}", name="planner")

        chat_response = await _maybe_await(
            self.llm.chat_completion(
                planner_messages,
                temperature=0.0,
                tools=self.tool_schemas,
            )
        )
        return self._plan_from_chat_response(chat_response)

    def _planner_prompt(self, turn_index: int) -> str:
        available = ", ".join(sorted(self.tools)) or "(no tools)"
        return (
            "You are planning the next action for a coding agent benchmark run.\n"
            f"Turn {turn_index} of {self.max_turns}.\n"
            "Return either a final answer or a small set of concrete tool calls.\n"
            "Budget matters. Prefer the smallest useful next step.\n"
            "When using tools, stay within the provided workspace and tools only.\n"
            f"Available tools: {available}.\n"
            "Return fields: thought, final, final_output, tool_calls.\n"
            "Set final=true only when you are genuinely done or blocked."
        )

    def _plan_from_chat_response(self, response: Any) -> Dict[str, Any]:
        if isinstance(response, dict):
            if isinstance(response.get("tool_calls"), list) or "final" in response:
                return response
            message = response.get("message")
            if isinstance(message, dict):
                tool_calls, malformed_tool_calls = self._normalize_tool_calls(message.get("tool_calls"))
                text = self._extract_text(message)
                if tool_calls or malformed_tool_calls:
                    return {
                        "thought": _trim_text(text or "Model requested tool work.", 600),
                        "final": False,
                        "final_output": "",
                        "tool_calls": tool_calls + malformed_tool_calls,
                    }
            for key in ("content", "text", "message"):
                if key in response:
                    try:
                        return _parse_json_object(response[key])
                    except Exception:
                        pass
        content = self._extract_text(response)
        try:
            return _parse_json_object(content)
        except Exception:
            return {
                "thought": _trim_text(content, 600),
                "final": True,
                "final_output": _trim_text(content, 2000),
                "tool_calls": [],
            }

    def _extract_text(self, response: Any) -> str:
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            for key in ("content", "text", "output_text"):
                if key in response and response[key] is not None:
                    return str(response[key])
            message = response.get("message")
            if isinstance(message, dict):
                for key in ("content", "text"):
                    if key in message and message[key] is not None:
                        return str(message[key])
        return str(response)

    async def _execute_tool_call(self, tool_call: Dict[str, Any]) -> None:
        tool_name = str(tool_call.get("tool_name") or "").strip()
        arguments = dict(tool_call.get("arguments") or {})
        purpose = str(tool_call.get("purpose") or "").strip()
        tool = self.tools.get(tool_name)
        if tool is None:
            self._append_message("tool", f"unknown tool: {tool_name}", name=tool_name)
            return

        try:
            preview = await self._call_tool_method(tool, "before_hitl", arguments)
            result = await self._call_tool_method(tool, "after_hitl", arguments, preview, "approved")
            payload = {
                "purpose": purpose,
                "arguments": arguments,
                "preview": preview,
                "result": result,
            }
            self._append_message("tool", _trim_text(_json_dumps(payload), 4000), name=tool_name)
        except Exception as exc:
            payload = {
                "purpose": purpose,
                "arguments": arguments,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=4),
            }
            self._append_message("tool", _trim_text(_json_dumps(payload), 4000), name=tool_name)

    async def _call_tool_method(self, tool: Any, method_name: str, *args: Any) -> Any:
        method = getattr(tool, method_name, None)
        if method is None:
            raise AttributeError(f"tool is missing required method {method_name}")
        try:
            return await _maybe_await(method(*args))
        except TypeError:
            if method_name == "before_hitl":
                return await _maybe_await(method(arguments=args[0]))
            if method_name == "after_hitl":
                return await _maybe_await(
                    method(arguments=args[0], preview=args[1], approval=args[2])
                )
            raise

    async def _synthesize_budget_stop(self) -> str:
        recent_tools = [m for m in self._messages if m.get("role") == "tool"][-3:]
        if recent_tools:
            return "Stopped at turn budget after tool activity. Review the latest tool results in agent._messages."
        return "Stopped at turn budget before a reliable final answer was produced."


def build_agent_backend(**kwargs: Any) -> BenchmarkAgentBackend:
    return BenchmarkAgentBackend(**kwargs)


__all__ = [
    "BenchmarkAgentBackend",
    "PLAN_SCHEMA",
    "REQUIRED_TOOL_NAMES",
    "build_agent_backend",
]
