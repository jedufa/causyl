from __future__ import annotations

import importlib.util
import importlib
import sys
from pathlib import Path
from types import ModuleType


_ROOT = Path(__file__).resolve().parents[1]


def load_tool_module(cache_key: str, relative_path: str) -> ModuleType:
    """Load a repo-root tools module without depending on the package name tools."""
    path = (_ROOT / relative_path).resolve()
    dotted = relative_path.removesuffix(".py").replace("/", ".")
    try:
        canonical = importlib.import_module(dotted)
        canonical_file = getattr(canonical, "__file__", "")
        if canonical_file and Path(str(canonical_file)).resolve() == path:
            return canonical
    except Exception:
        pass
    module_name = f"_causyl_root_tools_{cache_key}"
    cached = sys.modules.get(module_name)
    if isinstance(cached, ModuleType):
        return cached
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load root tool module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
