from __future__ import annotations

from pathlib import Path as _Path

from infra.module_parts_loader import load_module_parts as _load_module_parts

_load_module_parts(globals(), _Path(__file__).with_name("server_agents_parts"))
