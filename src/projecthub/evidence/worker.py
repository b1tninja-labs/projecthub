"""The evidence worker: take queued submissions, run the evidence check on each, and record a verdict.

An agent that believes a row is done calls `submit(lease_id, sha)` on the work server, which queues a row in
`submissions` in the lease database. A full check (L1, L3 and L4, see check.py) takes from seconds to
minutes, too long for a tool call, so this worker runs it out of band:

    queued -> running -> passed | failed

For each submission it finds the project's checkout under the workspace root, confirms the sha is a commit
there and is reachable from the default branch (main or master) or from the branch the row's lease names,
takes the base as the merge-base with the default branch, and runs `check.check` for exactly that row with
mutation on, in a child process with a time limit. The verdict goes into `verdicts`; the submission is
marked passed only when the check says ok for that row at that sha. A crash, a refusal or a timeout marks
it failed with the reason, in the same transaction that stores the verdict, so the two never disagree.

    projecthub evidence-worker [--once] [--root PATH] [--db PATH] [--timeout SECONDS]

The lease database is PROJECTHUB_LEASES_DB, else leases.sqlite in the state directory; the root is
PROJECTHUB_ROOT, else the workspace default.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from projecthub import paths, registry
from projecthub.db import parser as roadmap

DEFAULT_TIMEOUT = 30 * 60.0
DEFAULT_BRANCHES = ("main", "master")
SHA = re.compile(r"[0-9a-fA-F]{7,64}")
PROJECT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_key TEXT NOT NULL,
    sha TEXT NOT NULL,
    lease_id TEXT,
    at REAL NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL,
    row_key TEXT NOT NULL,
    sha TEXT NOT NULL,
    base TEXT NOT NULL,
    ok INTEGER NOT NULL,
    reasons TEXT NOT NULL,
    l1 TEXT NOT NULL,
    l3 TEXT NOT NULL,
    l4 TEXT NOT NULL,
    finished_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS verdicts_row ON verdicts(row_key, finished_at);
"""

# (repo, project, base, head, row, timeout) -> the check's record
Checker = Callable[[pathlib.Path, str, str, str, str, float], dict[str, Any]]


class Refused(Exception):
    """The submission cannot be checked: the reason goes into the verdict as it is."""


@dataclass
class Verdict:
    submission_id: int
    row_key: str
    sha: str
    base: str = ""
    ok: bool = False
    reasons: list[str] = field(default_factory=list)
    l1: dict[str, Any] = field(default_factory=dict)
    l3: dict[str, Any] = field(default_factory=dict)
    l4: dict[str, Any] = field(default_factory=dict)
    finished_at: float = 0.0

    @property
    def state(self) -> str:
        return "passed" if self.ok else "failed"


def default_db() -> pathlib.Path:
    named = paths.env("LEASES_DB")
    return pathlib.Path(named) if named else paths.state_dir() / "leases.sqlite"


def default_root() -> pathlib.Path:
    named = paths.env("ROOT")
    return pathlib.Path(named) if named else paths.ROOT


# ---------------------------------------------------------------------------------------------- database


def _connect(db_path: pathlib.Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


@contextmanager
def _write(db_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    """One write transaction, taken before the read it guards."""
    db = _connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def _claim(db_path: pathlib.Path) -> sqlite3.Row | None:
    """The oldest queued submission, marked running in the same transaction so two workers never share one."""
    with _write(db_path) as db:
        found = db.execute(
            "SELECT * FROM submissions WHERE state='queued' ORDER BY at, id LIMIT 1"
        ).fetchone()
        if found is not None:
            db.execute(
                "UPDATE submissions SET state='running' WHERE id=? AND state='queued'",
                (found["id"],),
            )
        return found


def _finish(db_path: pathlib.Path, verdict: Verdict) -> None:
    """Store the verdict and end the submission together: both land or neither does."""
    with _write(db_path) as db:
        db.execute(
            "INSERT INTO verdicts (submission_id, row_key, sha, base, ok, reasons, l1, l3, l4, finished_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                verdict.submission_id,
                verdict.row_key,
                verdict.sha,
                verdict.base,
                int(verdict.ok),
                json.dumps(verdict.reasons),
                json.dumps(verdict.l1),
                json.dumps(verdict.l3),
                json.dumps(verdict.l4),
                verdict.finished_at,
            ),
        )
        db.execute(
            "UPDATE submissions SET state=? WHERE id=?",
            (verdict.state, verdict.submission_id),
        )


def _lease_branch(db_path: pathlib.Path, lease_id: str | None) -> str:
    """The branch the lease names, if the leases table records one (a `branch` column); "" otherwise."""
    if not lease_id:
        return ""
    db = _connect(db_path)
    try:
        columns = {r["name"] for r in db.execute("PRAGMA table_info(leases)")}
        if "branch" not in columns:
            return ""
        found = db.execute(
            "SELECT branch FROM leases WHERE lease_id=?", (lease_id,)
        ).fetchone()
        return str(found["branch"] or "") if found is not None else ""
    finally:
        db.close()


def verdict_for(db_path: pathlib.Path, row_key: str) -> dict[str, Any] | None:
    """The latest verdict recorded for `row_key`, with its JSON fields decoded; None if there is none."""
    db = _connect(db_path)
    try:
        found = db.execute(
            "SELECT * FROM verdicts WHERE row_key=? ORDER BY finished_at DESC, id DESC LIMIT 1",
            (row_key,),
        ).fetchone()
    finally:
        db.close()
    if found is None:
        return None
    out = dict(found)
    out["ok"] = bool(out["ok"])
    for name in ("reasons", "l1", "l3", "l4"):
        out[name] = json.loads(out[name])
    return out


# ---------------------------------------------------------------------------------------------- git


def _git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _ref(repo: pathlib.Path, ref: str) -> str:
    done = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return done.stdout.strip() if done.returncode == 0 else ""


def _default_branch(repo: pathlib.Path) -> str:
    for name in DEFAULT_BRANCHES:
        for ref in (f"refs/heads/{name}", f"refs/remotes/origin/{name}"):
            if _ref(repo, ref):
                return ref
    raise Refused(f"{repo} has no {' or '.join(DEFAULT_BRANCHES)} branch")


def _reachable(repo: pathlib.Path, sha: str, ref: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", sha, ref).returncode == 0


def project_checkout(root: pathlib.Path, project: str) -> pathlib.Path:
    """The project's directory under `root`: the registry's, else the parser's naming rule."""
    named = {e.name: e.directory for e in registry.entries()}
    if project in named and named[project]:
        return root / named[project]
    return roadmap.project_dir(root, project)


def locate(
    root: pathlib.Path, row_key: str, sha: str, branch: str = ""
) -> tuple[pathlib.Path, str, str, str, str]:
    """(repo, project, row, full sha, base) for a submission, or Refused with the reason."""
    project, sep, row = row_key.partition(":")
    if not sep or not row or not PROJECT.fullmatch(project) or ".." in project:
        raise Refused(f"{row_key!r} is not a project:row key")
    if not SHA.fullmatch(sha):
        raise Refused(f"{sha!r} is not a commit id")
    repo = project_checkout(root, project)
    if not repo.is_dir():
        raise Refused(f"no checkout of {project} at {repo}")
    if _git(repo, "rev-parse", "--git-dir").returncode != 0:
        raise Refused(f"{repo} is not a git repository")
    full = _ref(repo, sha)
    if not full:
        raise Refused(f"{sha} is not a commit in {repo}")
    default = _default_branch(repo)
    allowed = [default]
    if branch:
        allowed += [
            r
            for r in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}")
            if _ref(repo, r)
        ]
    if not any(_reachable(repo, full, ref) for ref in allowed):
        names = ", ".join(r.removeprefix("refs/") for r in allowed)
        raise Refused(f"{full} is not reachable from {names}")
    done = _git(repo, "merge-base", full, default)
    base = done.stdout.strip()
    if done.returncode != 0 or not base:
        raise Refused(f"{full} shares no history with {default}")
    if base == full:
        # Already on the default branch: measure the commit against the one before it (its first parent).
        base = _ref(repo, f"{full}^1")
        if not base:
            raise Refused(
                f"{full} is a root commit: there is no base to measure it against"
            )
    return repo, project, row, full, base


# ---------------------------------------------------------------------------------------------- check

CHILD = """
import json, pathlib, sys
from projecthub.evidence import check
repo, project, base, head, row, out = sys.argv[1:]
record = check.check(pathlib.Path(repo), project, base, head, [row], mutate=True)
pathlib.Path(out).write_text(json.dumps(record), encoding="utf-8")
"""


def _kill_tree(process: subprocess.Popen[str]) -> None:
    """End the child and the pytest runs under it, so a timeout leaves nothing holding the temp tree."""
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        import signal

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.kill()


def check_in_child(
    repo: pathlib.Path, project: str, base: str, head: str, row: str, timeout: float
) -> dict[str, Any]:
    """`check.check` for one row with mutation on, in a child process: a crash or a hang cannot take the
    worker with it. Raises TimeoutError past `timeout` seconds and RuntimeError when the child fails."""
    package_root = pathlib.Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(package_root), os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    # On Windows, taskkill returns before the killed pytest runs release their handles, so removing the tree
    # right after a timeout can raise WinError 32. That error must not replace the real outcome (it turned a
    # timeout into "crash: PermissionError" about one run in three); a leftover temp folder is harmless.
    with tempfile.TemporaryDirectory(
        prefix="projecthub-worker-", ignore_cleanup_errors=True
    ) as tmp:
        out = pathlib.Path(tmp) / "record.json"
        process = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-c",
                CHILD,
                str(repo),
                project,
                base,
                head,
                row,
                str(out),
            ],
            cwd=tmp,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=sys.platform != "win32",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            process.communicate()
            raise TimeoutError(f"the check timed out after {timeout:g}s") from None
        if process.returncode != 0 or not out.is_file():
            tail = "\n".join((stdout + stderr).strip().splitlines()[-15:])
            raise RuntimeError(f"the check exited {process.returncode}: {tail}")
        record: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
        return record


def judge(verdict: Verdict, record: dict[str, Any], project: str, row: str) -> None:
    """Fill the verdict from the check's record. ok only if the record is ok, at this sha, for this row."""
    rows = record.get("rows") or []
    mine = [r for r in rows if r.get("row") == f"{project}:{row}"]
    if record.get("error"):
        verdict.reasons.append(str(record["error"]))
    if mine:
        audit = mine[0]
        verdict.l1, verdict.l3, verdict.l4 = (
            dict(audit.get("l1") or {}),
            dict(audit.get("l3") or {}),
            dict(audit.get("l4") or {}),
        )
        if audit.get("reason"):
            verdict.reasons.append(str(audit["reason"]))
    elif not record.get("error"):
        verdict.reasons.append(f"the check audited no row {project}:{row}")
    if record.get("head") != verdict.sha:
        verdict.reasons.append(
            f"the check ran at {record.get('head')}, not {verdict.sha}"
        )
    verdict.ok = (
        record.get("ok") is True
        and record.get("head") == verdict.sha
        and len(rows) == 1
        and len(mine) == 1
        and mine[0].get("ok") is True
    )
    if verdict.ok:
        verdict.reasons = []
    elif not verdict.reasons:
        verdict.reasons.append("the check did not report ok")


def handle(
    db_path: pathlib.Path,
    root: pathlib.Path,
    submission: sqlite3.Row,
    checker: Checker = check_in_child,
    timeout: float = DEFAULT_TIMEOUT,
) -> Verdict:
    """Check one running submission and record its verdict. Whatever happens, it ends passed or failed."""
    verdict = Verdict(
        int(submission["id"]), str(submission["row_key"]), str(submission["sha"])
    )
    try:
        branch = _lease_branch(db_path, submission["lease_id"])
        repo, project, row, verdict.sha, verdict.base = locate(
            root, verdict.row_key, verdict.sha, branch
        )
        record = checker(repo, project, verdict.base, verdict.sha, row, timeout)
        judge(verdict, record, project, row)
    except Refused as refused:
        verdict.ok, verdict.reasons = False, [str(refused)]
    except TimeoutError as timed_out:
        verdict.ok, verdict.reasons = False, [f"timeout: {timed_out}"]
    except (Exception, SystemExit) as crash:  # noqa: BLE001 - every failure becomes a failed verdict
        verdict.ok, verdict.reasons = False, [f"crash: {type(crash).__name__}: {crash}"]
    verdict.finished_at = time.time()
    _finish(db_path, verdict)
    return verdict


def run_once(
    db_path: pathlib.Path,
    root: pathlib.Path,
    limit: int | None = 1,
    checker: Checker = check_in_child,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[Verdict]:
    """Check up to `limit` queued submissions, oldest first (all of them when `limit` is None)."""
    done: list[Verdict] = []
    while limit is None or len(done) < limit:
        submission = _claim(db_path)
        if submission is None:
            break
        done.append(handle(db_path, root, submission, checker, timeout))
    return done


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub evidence-worker", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--once", action="store_true", help="check one submission, then exit"
    )
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=None,
        help="the workspace holding the checkouts",
    )
    parser.add_argument(
        "--db", type=pathlib.Path, default=None, help="the lease database"
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds per check"
    )
    args = parser.parse_args(argv)
    db_path = args.db or default_db()
    root = args.root or default_root()
    verdicts = run_once(db_path, root, 1 if args.once else None, timeout=args.timeout)
    if not verdicts:
        print("evidence-worker: no queued submission")
    for v in verdicts:
        print(
            f"{v.state.upper():<6}  #{v.submission_id}  {v.row_key}  {v.sha[:12]}  {'; '.join(v.reasons)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
