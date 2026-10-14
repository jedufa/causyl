from __future__ import annotations

import dataclasses
import os
import shlex
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Set

from .core import PipelineError


FORBIDDEN_EXECUTABLES = {
    "bash",
    "sh",
    "zsh",
    "fish",
    "cmd",
    "powershell",
    "pwsh",
    "sudo",
    "ssh",
    "scp",
    "curl",
    "wget",
}
FORBIDDEN_TOKENS = {"&&", "||", ";", "|", ">", ">>", "<", "1>", "2>", "2>&1", "&"}
DEFAULT_ALLOWED_EXECUTABLES = {
    "python",
    "python3",
    "pytest",
    "ruff",
    "mypy",
    "pyright",
    "semgrep",
    "tox",
    "nox",
}
SAFE_PYTHON_MODULES = {
    "compileall",
    "mypy",
    "py_compile",
    "pyright",
    "pytest",
    "ruff",
    "semgrep",
    "tox",
    "unittest",
}
STATIC_SAFE_EXECUTABLES = {
    "mypy",
    "pyright",
    "ruff",
    "semgrep",
}
STATIC_SAFE_PYTHON_MODULES = {
    "compileall",
    "mypy",
    "py_compile",
    "pyright",
    "ruff",
    "semgrep",
}


@dataclasses.dataclass(frozen=True)
class CommandSpec:
    name: str
    command: List[str]
    timeout_seconds: int
    required: bool
    source: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "command": list(self.command),
            "timeout_seconds": int(self.timeout_seconds),
            "required": bool(self.required),
            "source": self.source,
        }


def _contains_control_chars(value: str) -> bool:
    return any(ord(ch) < 32 for ch in str(value or "") if ch not in {"\t"})


def _reject_unsafe_token(token: str, *, token_index: int) -> None:
    txt = str(token or "")
    if txt in FORBIDDEN_TOKENS:
        raise PipelineError(
            "unsafe_command_token",
            "command contains forbidden shell token",
            details={"token": txt, "token_index": token_index},
            exit_code=6,
        )
    if _contains_control_chars(txt) or "\n" in txt or "\r" in txt or "`" in txt or "$(" in txt:
        raise PipelineError(
            "unsafe_command_token",
            "command contains unsafe control or interpolation characters",
            details={"token": txt, "token_index": token_index},
            exit_code=6,
        )
    if token_index > 0:
        if txt.startswith("/") or txt.startswith("~") or txt.startswith("../") or txt.startswith("..\\") or "/../" in txt or "\\..\\" in txt:
            raise PipelineError(
                "unsafe_command_argument",
                "command argument escapes workspace policy",
                details={"token": txt, "token_index": token_index},
                exit_code=6,
            )


def allowed_executable_basenames(config: Mapping[str, object], *, extra: Optional[Iterable[str]] = None) -> Set[str]:
    tools = config.get("tools", {}) if isinstance(config, Mapping) else {}
    basenames: Set[str] = set(DEFAULT_ALLOWED_EXECUTABLES)
    if isinstance(tools, Mapping):
        for key, value in tools.items():
            if not str(key).endswith("_path"):
                continue
            txt = str(value or "").strip()
            if not txt:
                continue
            basenames.add(Path(txt).name)
    for value in list(extra or []):
        txt = Path(str(value or "").strip()).name
        if txt:
            basenames.add(txt)
    return basenames


def normalize_command(value: object) -> List[str]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise PipelineError("invalid_command", "command string must not be empty", exit_code=3)
        try:
            tokens = shlex.split(text)
        except ValueError as exc:
            raise PipelineError("invalid_command", f"command string could not be parsed: {exc}", details={"command": text}, exit_code=3) from exc
        if not tokens:
            raise PipelineError("invalid_command", "command string must not be empty", exit_code=3)
        return [str(token) for token in tokens]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        tokens = [str(token) for token in list(value)]
        if not tokens:
            raise PipelineError("invalid_command", "command list must not be empty", exit_code=3)
        return tokens
    raise PipelineError("invalid_command", "command must be a string or list", details={"value": value}, exit_code=3)


def validate_command(tokens: Sequence[str], *, allowed_executables: Iterable[str]) -> List[str]:
    argv = [str(token) for token in list(tokens)]
    if not argv:
        raise PipelineError("invalid_command", "command must not be empty", exit_code=3)
    allowed = {Path(str(value or "").strip()).name for value in list(allowed_executables or []) if str(value or "").strip()}
    executable = Path(argv[0]).name
    if executable in FORBIDDEN_EXECUTABLES:
        raise PipelineError(
            "forbidden_executable",
            "command executable is forbidden by verification policy",
            details={"executable": executable},
            exit_code=6,
        )
    if allowed and executable not in allowed:
        raise PipelineError(
            "executable_not_allowlisted",
            "command executable is not allowlisted for verification",
            details={"executable": executable, "allowed_executables": sorted(allowed)},
            exit_code=6,
        )
    for idx, token in enumerate(argv):
        _reject_unsafe_token(token, token_index=idx)
    if executable in {"python", "python3"}:
        _validate_python_invocation(argv)
    return argv


def _validate_python_invocation(argv: Sequence[str]) -> None:
    arguments = [str(token or "") for token in list(argv)[1:]]
    if "-c" in arguments:
        raise PipelineError(
            "unsafe_python_inline",
            "inline python commands are not allowed in verification profiles",
            details={"command": list(argv)},
            exit_code=6,
        )
    if "-m" in arguments:
        module_index = arguments.index("-m")
        if module_index == len(arguments) - 1:
            raise PipelineError(
                "invalid_command",
                "python -m requires a module name",
                details={"command": list(argv)},
                exit_code=3,
            )
        module_name = str(arguments[module_index + 1] or "").strip()
        if module_name not in SAFE_PYTHON_MODULES:
            raise PipelineError(
                "python_module_not_allowlisted",
                "python module is not allowlisted for verification",
                details={"module": module_name, "command": list(argv)},
                exit_code=6,
            )


def validate_static_only_command(tokens: Sequence[str]) -> List[str]:
    argv = [str(token) for token in list(tokens)]
    if not argv:
        raise PipelineError("invalid_command", "command must not be empty", exit_code=3)
    executable = Path(argv[0]).name
    if executable in {"python", "python3"}:
        arguments = argv[1:]
        if len(arguments) >= 2 and arguments[0] == "-m" and arguments[1] in STATIC_SAFE_PYTHON_MODULES:
            if arguments[1] == "ruff" and "--fix" in arguments:
                raise PipelineError(
                    "static_command_not_readonly",
                    "static verification commands must not request in-place fixes",
                    details={"command": argv},
                    exit_code=6,
                )
            return argv
        raise PipelineError(
            "static_command_not_allowed",
            "python-based static verification commands must use an allowlisted static module",
            details={"command": argv},
            exit_code=6,
        )
    if executable not in STATIC_SAFE_EXECUTABLES:
        raise PipelineError(
            "static_command_not_allowed",
            "static verification commands must be read-only static analysis tools",
            details={"command": argv, "executable": executable},
            exit_code=6,
        )
    if executable == "ruff":
        arguments = argv[1:]
        if "--fix" in arguments or ("format" in arguments and "--check" not in arguments):
            raise PipelineError(
                "static_command_not_readonly",
                "ruff static verification commands must be read-only",
                details={"command": argv},
                exit_code=6,
            )
    return argv


def build_command_spec(
    *,
    name: str,
    command: object,
    timeout_seconds: int,
    required: bool,
    source: str,
    allowed_executables: Iterable[str],
) -> CommandSpec:
    argv = normalize_command(command)
    validated = validate_command(argv, allowed_executables=allowed_executables)
    timeout = int(timeout_seconds)
    if timeout <= 0:
        raise PipelineError(
            "invalid_command_timeout",
            "verification command timeout must be positive",
            details={"name": name, "timeout_seconds": timeout_seconds},
            exit_code=3,
        )
    return CommandSpec(
        name=str(name or Path(validated[0]).name),
        command=list(validated),
        timeout_seconds=timeout,
        required=bool(required),
        source=str(source or "verification_profile"),
    )


def canonical_command_string(command: Sequence[str]) -> str:
    return " ".join(shlex.quote(str(token)) for token in list(command or []))


def executable_exists(executable: str, *, env: Optional[Mapping[str, str]] = None) -> bool:
    if not executable:
        return False
    if os.path.isabs(executable) and Path(executable).exists():
        return True
    search_path = None if env is None else env.get("PATH")
    return shutil.which(executable, path=search_path) is not None


try:
    import shutil
except Exception:  # pragma: no cover - stdlib import guard only
    shutil = None  # type: ignore
