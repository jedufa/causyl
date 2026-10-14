from __future__ import annotations

import dataclasses
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from .core import PipelineError

ExecutionToolHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclasses.dataclass(frozen=True)
class ExecutionToolSpec:
    name: str
    stage_range: str
    description: str
    consumes: List[str]
    produces: str
    handler: Optional[ExecutionToolHandler] = None


class ExecutionToolRegistry:
    def __init__(self, specs: Optional[Iterable[ExecutionToolSpec]] = None) -> None:
        self._specs: Dict[str, ExecutionToolSpec] = {}
        for spec in list(specs or []):
            self.register(spec)

    def register(self, spec: ExecutionToolSpec) -> None:
        self._specs[str(spec.name)] = spec

    def get(self, name: str) -> ExecutionToolSpec:
        spec = self._specs.get(str(name))
        if spec is None:
            raise PipelineError("unknown_execution_tool", f"Unknown execution tool: {name}", details={"tool": name}, exit_code=3)
        return spec

    def list_specs(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "stage_range": spec.stage_range,
                "description": spec.description,
                "consumes": list(spec.consumes),
                "produces": spec.produces,
                "implemented": spec.handler is not None,
            }
            for spec in self._specs.values()
        ]

    def execute(self, name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        spec = self.get(name)
        if spec.handler is None:
            raise PipelineError(
                "execution_tool_unimplemented",
                f"Execution tool not implemented yet: {name}",
                details={"tool": name, "stage_range": spec.stage_range},
                exit_code=3,
            )
        return dict(spec.handler(payload))


def build_default_execution_tool_registry() -> ExecutionToolRegistry:
    return ExecutionToolRegistry(
        [
            ExecutionToolSpec(
                name="generate_patch",
                stage_range="8",
                description="Generate a bounded patch artifact from the Stage 7 execution contract.",
                consumes=["execution_contract"],
                produces="PatchArtifact",
            ),
            ExecutionToolSpec(
                name="validate_patch",
                stage_range="9",
                description="Validate and normalize a generated patch artifact before apply.",
                consumes=["PatchArtifact"],
                produces="ValidatedArtifact",
            ),
            ExecutionToolSpec(
                name="apply_patch",
                stage_range="10",
                description="Apply a validated patch artifact into an isolated workspace.",
                consumes=["ValidatedArtifact"],
                produces="ApplyResult",
            ),
            ExecutionToolSpec(
                name="verify_static",
                stage_range="11",
                description="Run bounded deterministic static verification inside a downstream isolated workspace.",
                consumes=["ApplyResult", "execution_contract"],
                produces="StaticVerificationResult",
            ),
            ExecutionToolSpec(
                name="verify_runtime",
                stage_range="12",
                description="Run bounded deterministic runtime verification inside a downstream isolated workspace.",
                consumes=["ApplyResult", "StaticVerificationResult", "execution_contract"],
                produces="RuntimeVerificationResult",
            ),
            ExecutionToolSpec(
                name="verify_contract",
                stage_range="13",
                description="Compare Stage 7 execution contract requirements against observed deterministic evidence.",
                consumes=["ApplyResult", "StaticVerificationResult", "RuntimeVerificationResult", "execution_contract"],
                produces="ContractVerificationResult",
            ),
        ]
    )
