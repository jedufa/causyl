from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List

class JeffBackendError(RuntimeError):
    pass


@dataclass
class Proposal:
    summary: str
    operations: List[Dict[str, Any]]
    checks: List[str]
    claimed_changed_files: List[str]


def _parse_json_object(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].lstrip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise JeffBackendError("proposal must be a json object")
    return parsed


class OllamaBackend:
    def __init__(self, *, model: str) -> None:
        try:
            from langchain_ollama import ChatOllama
        except ModuleNotFoundError as exc:
            raise JeffBackendError("langchain_ollama is required for provider=ollama") from exc
        self.model = str(model or "").strip() or "qwen2.5-coder:14b"
        self._llm = ChatOllama(model=self.model, temperature=0)

    def propose(self, *, task: str, inspection: Dict[str, Any], session_summary: str) -> Proposal:
        prompt = "\n".join(
            [
                "You are Jeff, a local coding assistant.",
                "Return JSON only.",
                'Required keys: "summary", "operations", "checks".',
                'Each operation must be an object with keys: "path", "content".',
                'If context is insufficient, return {"summary":"INSUFFICIENT_CONTEXT","operations":[],"checks":[]}.',
                "",
                f"Task: {task}",
                f"Session summary: {session_summary}",
                f"Inspection: {json.dumps(inspection, ensure_ascii=False)}",
            ]
        )
        response = self._llm.invoke(prompt)
        payload = _parse_json_object(getattr(response, "content", ""))
        operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
        checks = [str(item or "").strip() for item in list(payload.get("checks") or []) if str(item or "").strip()]
        claimed = [str(item or "").strip() for item in list(payload.get("claimed_changed_files") or []) if str(item or "").strip()]
        if not claimed:
            claimed = [str(op.get("path") or "").strip() for op in operations if isinstance(op, dict) and str(op.get("path") or "").strip()]
        return Proposal(
            summary=str(payload.get("summary") or "").strip(),
            operations=[dict(op) for op in operations if isinstance(op, dict)],
            checks=checks,
            claimed_changed_files=claimed,
        )


def create_backend(*, provider: str, model: str) -> Any:
    selected = str(provider or "ollama").strip().lower()
    if selected == "ollama":
        return OllamaBackend(model=model)
    raise JeffBackendError(f"unsupported provider: {selected}")
