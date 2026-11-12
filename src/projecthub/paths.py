"""Where the workspace and projecthub's own state live, and which paths may hold generated records."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PREFIX = "PROJECTHUB_"
# The prefix this system's variables had before it was named projecthub. Read after PREFIX, never written,
# so a CI secret, a shell profile or a note that still spells the old name keeps working.
LEGACY_PREFIX = "FORGE_"


def env(name: str, default: str = "", *, keep_empty: bool = False) -> str:
    """The setting `name`: PROJECTHUB_<name>, else the older spelling of it, else `default`.

    An empty variable counts as unset, so it falls through to the next one. With `keep_empty`, a variable
    that is set but empty is an answer in its own right, for a setting whose empty value means something.
    """
    for key in (PREFIX + name, LEGACY_PREFIX + name):
        value = os.environ.get(key)
        if value or (keep_empty and value is not None):
            return value
    return default


ROOT = Path(env("ROOT", "D:/code"))


def state_dir() -> Path:
    """One directory per machine, outside every work tree, so all sessions and worktrees share one queue.

    New installs use `<base>/projecthub`. A machine that already has the directory this system used before
    it was renamed (`<base>/forge`) and no `projecthub` directory keeps using the old one: the lease
    database inside it is live state, and moving it under a running server would split the queue in two.
    """
    base = Path(
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_STATE_HOME")
        or str(Path.home() / ".local" / "state")
    )
    new, old = base / "projecthub", base / "forge"
    if not new.exists() and old.is_dir():
        return old
    return new


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
