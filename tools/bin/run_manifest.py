"""tools.run_manifest

PHASE 2/3 — Local Manifest Runner (Batch execution, local node)

This module is intentionally a *thin CLI shim*.
The real implementation lives in: tools/manifest/manifest_runner.py

Why this file exists (because humans):
- Lots of docs, scripts, and muscle memory invoke: `python -m tools.run_manifest ...`
- Phase A split moved the orchestration logic into `tools.manifest.manifest_runner`

This shim MUST:
- Import the implementation entrypoint (main)
- Actually call it when executed as a module
- Provide an optional, explicit import-location debug banner to catch cases where
  Python is executing the *wrong* `tools` package (e.g., site-packages shadowing
  your repo checkout).

Debug banner:
  Set EXPERIMENT_DEBUG_RUN_MANIFEST=1 to print module path + sys.path[0].

No behavioral changes are intended when the env var is not set.
"""

from __future__ import annotations

import os
import sys
import traceback


def _maybe_print_import_debug() -> None:
    """Optionally print import-path debug info.

    This helps diagnose the classic failure mode:
      - You run `python -m tools.run_manifest ...`
      - Python imports a different `tools` package than your repo's `tools/`
      - The command appears to "do nothing" because you're not running your code.

    Only prints when EXPERIMENT_DEBUG_RUN_MANIFEST=1.
    """
    if os.environ.get("EXPERIMENT_DEBUG_RUN_MANIFEST", "").strip() != "1":
        return

    try:
        import tools  # noqa: WPS433 (local import by design for debugging)

        sys.stderr.write("[run_manifest debug] tools package file: %s\n" % getattr(tools, "__file__", "<no __file__>"))
        sys.stderr.write("[run_manifest debug] run_manifest file: %s\n" % __file__)
        sys.stderr.write("[run_manifest debug] sys.path[0]: %s\n" % (sys.path[0] if sys.path else "<empty>"))
        sys.stderr.flush()
    except Exception:
        # Debug helper must never crash the real command.
        sys.stderr.write("[run_manifest debug] failed to print import debug info\n")
        sys.stderr.flush()


def _run() -> int:
    """Run the manifest runner.

    Returns
    -------
    int
        Process exit code (0=ok, nonzero=errors).
    """
    _maybe_print_import_debug()

    # Import here so the debug banner can report the importing module path first.
    from tools.manifest.manifest_runner import main  # noqa: WPS433 (intentional local import)

    return int(main())


if __name__ == "__main__":
    try:
        raise SystemExit(_run())
    except SystemExit:
        raise
    except Exception:
        # If anything bubbles up here, you should see it. Silent failures are for cowards.
        traceback.print_exc()
        raise SystemExit(1)
