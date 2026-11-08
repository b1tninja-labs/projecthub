"""Where the workspace and projecthub's own state live, and which paths may hold generated records."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(os.environ.get("FORGE_ROOT") or "D:/code")


def state_dir() -> Path:
    """One directory per machine, outside every work tree, so all sessions and worktrees share one queue."""
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_STATE_HOME")
        or str(Path.home() / ".local" / "state")
    )
    return Path(base) / "forge"


def refuse_path(path: Path) -> str:
    """Why `path` may not hold an evidence record, or "" if it may.

    Inside the workspace or inside any git work tree, a results file is one `git add -A` away from being
    committed as if it were source, which this workspace has already had to amend out once (AGENTS.md).
    """
    target = path.resolve()
    if target == ROOT.resolve() or ROOT.resolve() in target.parents:
        return f"{target} is inside the workspace {ROOT}; write evidence outside it"
    probe = next((p for p in [target, *target.parents] if p.is_dir()), None)
    if probe is not None:
        done = subprocess.run(  # noqa: S603, S607
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if done.returncode == 0:
            return f"{target} is inside the git work tree {done.stdout.strip()}"
    return ""
