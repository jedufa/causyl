"""infra.webui.server

Shim entrypoint.

This project historically kept the entire WebUI server in one gigantic file.
Humans and 10k-line diffs are not compatible lifeforms, so the implementation
has been split into focused modules:

- server_common.py (imports, config, core utilities)
- server_* (domain logic)
- server_routes_and_main.py (HTTP handler + CLI)

This file remains as the stable import and execution target.
"""

from __future__ import annotations

# Re-export everything that other modules/scripts may import from infra.webui.server.
from infra.webui.server_common import *  # noqa: F401,F403
from infra.webui.server_run_discovery import *  # noqa: F401,F403
from infra.webui.server_deprecated_index import *  # noqa: F401,F403
from infra.webui.server_ui_metadata import *  # noqa: F401,F403
from infra.webui.server_data_precedence import *  # noqa: F401,F403
from infra.webui.server_tail_parsers import *  # noqa: F401,F403
from infra.webui.server_timeline import *  # noqa: F401,F403
from infra.webui.server_run_meta import *  # noqa: F401,F403
from infra.webui.server_changesets import *  # noqa: F401,F403
from infra.webui.server_exports import *  # noqa: F401,F403
from infra.webui.server_archives import *  # noqa: F401,F403
from infra.webui.server_command_submission import *  # noqa: F401,F403
from infra.webui.server_branch_planning import *  # noqa: F401,F403
from infra.webui.server_run_summaries import *  # noqa: F401,F403
from infra.webui.server_http_core import *  # noqa: F401,F403
from infra.webui.server_routes_and_main import main  # noqa: F401

if __name__ == "__main__":
    raise SystemExit(main())
