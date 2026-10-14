"""Compatibility wrapper over the shared experiment run-job runtime.

The canonical implementation lives in :mod:`experiments_shared.run_job`. This
module re-exports the shared surface so existing imports such as
``tools.core.run_job._make_wrapper_folder_name`` and ``python -m tools.core.run_job``
continue to work while lifecycle truth remains owned by the shared substrate.
"""

from __future__ import annotations

import os
import sys

_THIS_FILE = os.path.abspath(__file__)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(_THIS_FILE), os.pardir, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from experiments_shared.run_job import *  # noqa: F401,F403,E402
from experiments_shared.run_job import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
