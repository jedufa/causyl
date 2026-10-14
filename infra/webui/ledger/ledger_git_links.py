"""infra.webui.ledger.ledger_git_links

Best-effort git linkage for ledger items.

We intentionally keep this "best-effort" and non-fatal:
- If git is unavailable, return empty.
- If repo isn't a git checkout (e.g., snapshot zip), return empty.

We link commits to a ledger UID by grepping commit messages.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List


def _looks_like_git_repo(repo_root: Path) -> bool:
    try:
        p = Path(repo_root) / ".git"
        return p.exists()
    except Exception:
        return False


def list_commits_for_uid(repo_root: Path, uid: str, *, limit: int = 50) -> List[Dict[str, Any]]:
    """Return best-effort list of commits referencing uid.

    Args:
        repo_root: repo root path.
        uid: ledger UID (e.g. PIS-...)
        limit: max commits

    Returns:
        List of {sha, subject, utc} dicts, newest first.
    """

    rr = Path(repo_root).resolve()
    if not _looks_like_git_repo(rr):
        return []

    lim = int(limit) if int(limit) > 0 else 50
    lim = max(1, min(500, lim))

    # Use a pretty format that's easy to parse.
    # %H full sha, %ct commit time (unix), %s subject.
    fmt = "%H\t%ct\t%s"
    try:
        out = subprocess.check_output(
            ["git", "log", f"-n{lim}", "--pretty=format:" + fmt, "--grep", str(uid)],
            cwd=str(rr),
            stderr=subprocess.STDOUT,
            timeout=2.0,
        )
    except Exception:
        return []

    lines = out.decode("utf-8", errors="replace").splitlines()
    res: List[Dict[str, Any]] = []
    for line in lines:
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        sha, ct, subj = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            continue
        try:
            ts = int(ct)
        except Exception:
            ts = 0
        res.append({"sha": sha.lower(), "commit_time_unix": ts, "subject": subj})

    # Newest first (git already returns newest first).
    return res
