from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def load_module_parts(module_globals: Dict[str, Any], parts_dir: Path) -> None:
    """Execute numbered module part files into one shared module namespace."""
    module_globals.setdefault("__builtins__", __builtins__)
    ordered_parts = sorted(
        path
        for path in parts_dir.iterdir()
        if path.is_file() and path.suffix == ".py" and not path.name.startswith("__")
    )
    if not ordered_parts:
        raise RuntimeError(f"no module parts found in {parts_dir}")
    for part_path in ordered_parts:
        source = part_path.read_text(encoding="utf-8")
        code = compile(source, str(part_path), "exec")
        exec(code, module_globals)
