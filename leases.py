"""Leases: who is working on which ROADMAP row right now, minted by the server and expiring on their own.

Why not the GitHub assignee. Every agent session here acts as the same account, so `claim()` could not
tell two agents apart: the second one read "already yours". A lease is a server-minted opaque handle
(UUIDv4) with a time to live. Holding the handle is the capability: heartbeat, release and block all
take it, and a lease that misses its heartbeat expires by itself, so a crashed agent cannot hold a row
forever. The assignee and issue comments may mirror a lease for people to see; they never decide it.

Where it lives. A SQLite file in sdk-forge/cache/ (gitignored), acquired inside BEGIN IMMEDIATE so two
agents racing for one row get exactly one lease. It writes nothing else: not ROADMAP.md, not GitHub.
That is why this is its own opt-in MCP server (`forge-lease`) rather than tools on the read-only planner:
a client that registers only the planner cannot reach a write of any kind.

State: live -> released (handoff / pr_opened / abandoned) | expired | blocked. A block of the same kind on
the same row more than twice escalates: the row needs a person, not another attempt (the Hermes rule).

Usage:
    python leases.py                 # speaks MCP on stdio (forge-lease)
    python leases.py list            # live leases, for a person
    python leases.py steal <lease_id>  # human-only: end someone else's lease
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypedDict

DEFAULT_DB = pathlib.Path(__file__).resolve().parent / "cache" / "leases.sqlite"
DEFAULT_TTL = 30 * 60
MAX_TTL = 4 * 60 * 60
OUTCOMES = ("handoff", "pr_opened", "abandoned")
BLOCK_KINDS = ("dependency", "spec_question", "decision_needed", "env")
ESCALATE_AFTER = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
    lease_id TEXT PRIMARY KEY,
    row_key TEXT NOT NULL,
    holder TEXT NOT NULL,
    scope_files TEXT NOT NULL,
    ttl REAL NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    state TEXT NOT NULL,
    outcome TEXT,
    note TEXT,
    pr_url TEXT
);
CREATE INDEX IF NOT EXISTS leases_row ON leases(row_key, state);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    lease_id TEXT,
    row_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class LeaseError(Exception):
    """A refusal with a stable code first, so a client can branch on it: `LEASE_EXPIRED: ...`."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        super().__init__(f"{code}: {reason}")


@dataclass(frozen=True)
class Lease:
    lease_id: str
    row_key: str
    holder: str
    scope_files: list[str]
    acquired_at: float
    expires_at: float
    state: str


def row_key(project: str, row_id: str) -> str:
    return f"{project}:{row_id}"


class Store:
    def __init__(
        self, path: pathlib.Path | None = None, now: Callable[[], float] = time.time
    ) -> None:
        self.path = path or pathlib.Path(
            os.environ.get("FORGE_LEASES_DB") or DEFAULT_DB
        )
        self.now = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One write transaction, taken before any read, so a check and the write it guards cannot interleave."""
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _expire(self, db: sqlite3.Connection) -> None:
        now = self.now()
        for row in db.execute(
            "SELECT lease_id, row_key FROM leases WHERE state='live' AND expires_at <= ?",
            (now,),
        ):
            self._event(
                db,
                row["lease_id"],
                row["row_key"],
                "expire",
                "no heartbeat within the TTL",
            )
        db.execute(
            "UPDATE leases SET state='expired' WHERE state='live' AND expires_at <= ?",
            (now,),
        )

    def _event(
        self,
        db: sqlite3.Connection,
        lease_id: str | None,
        key: str,
        kind: str,
        detail: str,
    ) -> None:
        db.execute(
            "INSERT INTO events(at, lease_id, row_key, kind, detail) VALUES (?, ?, ?, ?, ?)",
            (self.now(), lease_id, key, kind, detail),
        )

    def _live(self, db: sqlite3.Connection, lease_id: str) -> sqlite3.Row:
        found = db.execute(
            "SELECT * FROM leases WHERE lease_id=?", (lease_id,)
        ).fetchone()
        if found is None:
            raise LeaseError("LEASE_UNKNOWN", f"no lease {lease_id}")
        if found["state"] == "expired" or (
            found["state"] == "live" and found["expires_at"] <= self.now()
        ):
            raise LeaseError(
                "LEASE_EXPIRED",
                f"{found['row_key']}: the lease expired; acquire it again",
            )
        if found["state"] != "live":
            raise LeaseError(
                "LEASE_ENDED", f"{found['row_key']}: the lease is {found['state']}"
            )
        return found

    # -- writes ------------------------------------------------------------------------------------

    def acquire(
        self,
        project: str,
        row_id: str,
        holder: str = "agent",
        scope_files: list[str] | None = None,
        ttl: float = DEFAULT_TTL,
    ) -> Lease:
        """A new lease on a row with no live lease. Readiness is the caller's check; this one is exclusivity."""
        key = row_key(project, row_id)
        span = max(60.0, min(float(ttl), MAX_TTL))
        with self._write() as db:
            self._expire(db)
            held = db.execute(
                "SELECT lease_id, holder, expires_at FROM leases WHERE row_key=? AND state='live'",
                (key,),
            ).fetchone()
            if held is not None:
                raise LeaseError(
                    "LEASE_HELD",
                    f"{key} is leased by {held['holder']} until {_iso(held['expires_at'])}",
                )
            now = self.now()
            lease = Lease(
                str(uuid.uuid4()),
                key,
                holder,
                sorted(scope_files or []),
                now,
                now + span,
                "live",
            )
            db.execute(
                "INSERT INTO leases VALUES (?, ?, ?, ?, ?, ?, ?, 'live', NULL, NULL, NULL)",
                (
                    lease.lease_id,
                    key,
                    holder,
                    json.dumps(lease.scope_files),
                    span,
                    now,
                    lease.expires_at,
                ),
            )
            self._event(
                db, lease.lease_id, key, "acquire", f"{holder}, ttl {int(span)}s"
            )
            return lease

    def heartbeat(self, lease_id: str) -> float:
        with self._write() as db:
            found = self._live(db, lease_id)
            expires = self.now() + found["ttl"]
            db.execute(
                "UPDATE leases SET expires_at=? WHERE lease_id=?", (expires, lease_id)
            )
            return expires

    def release(
        self, lease_id: str, outcome: str, note: str = "", pr_url: str = ""
    ) -> str:
        if outcome not in OUTCOMES:
            raise LeaseError(
                "BAD_OUTCOME", f"outcome must be one of {', '.join(OUTCOMES)}"
            )
        if outcome == "pr_opened" and not pr_url:
            raise LeaseError(
                "PR_URL_REQUIRED", "a pr_opened release names the pull request"
            )
        with self._write() as db:
            found = self._live(db, lease_id)
            db.execute(
                "UPDATE leases SET state='released', outcome=?, note=?, pr_url=? WHERE lease_id=?",
                (outcome, note, pr_url or None, lease_id),
            )
            self._event(
                db, lease_id, found["row_key"], f"release:{outcome}", note or pr_url
            )
            return "released"

    def block(self, lease_id: str, kind: str, detail: str) -> dict[str, object]:
        if kind not in BLOCK_KINDS:
            raise LeaseError(
                "BAD_KIND", f"kind must be one of {', '.join(BLOCK_KINDS)}"
            )
        if not detail.strip():
            raise LeaseError(
                "DETAIL_REQUIRED",
                "say what blocks the row, so the next reader does not rediscover it",
            )
        with self._write() as db:
            found = self._live(db, lease_id)
            key = found["row_key"]
            db.execute(
                "UPDATE leases SET state='blocked', note=? WHERE lease_id=?",
                (f"{kind}: {detail}", lease_id),
            )
            self._event(db, lease_id, key, f"block:{kind}", detail)
            times = db.execute(
                "SELECT COUNT(*) FROM events WHERE row_key=? AND kind=?",
                (key, f"block:{kind}"),
            ).fetchone()[0]
            escalate = times > ESCALATE_AFTER or kind == "decision_needed"
            return {
                "state": "Blocked",
                "row_key": key,
                "times_blocked_this_way": times,
                "needs_a_person": escalate,
            }

    def steal(self, lease_id: str, reason: str) -> str:
        """Human-only (CLI): end a lease someone else holds."""
        with self._write() as db:
            found = db.execute(
                "SELECT row_key, state FROM leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            if found is None:
                raise LeaseError("LEASE_UNKNOWN", f"no lease {lease_id}")
            db.execute(
                "UPDATE leases SET state='released', outcome='stolen', note=? WHERE lease_id=?",
                (reason, lease_id),
            )
            self._event(db, lease_id, found["row_key"], "steal", reason)
            return "released"

    # -- reads -------------------------------------------------------------------------------------

    def live(self) -> list[Lease]:
        with self._write() as db:
            self._expire(db)
            rows = db.execute(
                "SELECT * FROM leases WHERE state='live' ORDER BY row_key"
            ).fetchall()
        return [
            Lease(
                r["lease_id"],
                r["row_key"],
                r["holder"],
                json.loads(r["scope_files"]),
                r["acquired_at"],
                r["expires_at"],
                r["state"],
            )
            for r in rows
        ]

    def live_keys(self) -> set[str]:
        return {lease.row_key for lease in self.live()}

    def held(self, key: str) -> Lease | None:
        return next((lease for lease in self.live() if lease.row_key == key), None)

    def events(self, key: str = "", limit: int = 50) -> list[dict[str, object]]:
        db = self._connect()
        try:
            query = (
                "SELECT * FROM events"
                + (" WHERE row_key=?" if key else "")
                + " ORDER BY id DESC LIMIT ?"
            )
            rows = db.execute(query, (key, limit) if key else (limit,)).fetchall()
        finally:
            db.close()
        return [
            {
                "at": _iso(r["at"]),
                "lease_id": r["lease_id"],
                "row_key": r["row_key"],
                "kind": r["kind"],
                "detail": r["detail"],
            }
            for r in rows
        ]


def _iso(stamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp))


# -- the opt-in MCP server ----------------------------------------------------------------------------


class AcquireOut(TypedDict):
    lease_id: str
    row_key: str
    expires_at: str
    scope_files: list[str]


class HeartbeatOut(TypedDict):
    lease_id: str
    expires_at: str


class ReleaseOut(TypedDict):
    lease_id: str
    state: str


class BlockOut(TypedDict):
    state: str
    row_key: str
    times_blocked_this_way: int
    needs_a_person: bool


class LeasesOut(TypedDict):
    live: list[dict[str, object]]
    recent_events: list[dict[str, object]]


INSTRUCTIONS = (
    "Leases on ROADMAP rows. Acquire one before working a row that forge_next lists as ready, heartbeat it "
    f"at least every {DEFAULT_TTL // 60} minutes (the default TTL), and release it when you hand off, open a pull "
    "request, or abandon the row. A lease is local bookkeeping: it never marks a row done, never edits "
    "ROADMAP.md, and never touches GitHub. If the same block recurs, stop and ask a person."
)


def build(store: Store | None = None, ready: Callable[[str, str], str] | None = None):  # type: ignore[no-untyped-def]
    """The forge-lease server. `ready(project, row_id)` returns "" if the row may be leased, else why not."""
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    leases = store or Store()
    check = ready or _ready_from_planner
    app = MCPServer(name="forge-lease", instructions=INSTRUCTIONS)
    write = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    )
    read = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )

    def guarded(call):  # type: ignore[no-untyped-def]
        try:
            return call()
        except LeaseError as error:
            raise ToolError(str(error)) from error

    @app.tool(
        description=(
            f"Lease a ready row. TTL defaults to {DEFAULT_TTL}s and is capped at {MAX_TTL}s; heartbeat before it "
            "runs out. Refuses a row that is not ready (inactive project, plan directory, unbuilt dependency) or "
            "already leased. scope_files is advisory: the files you expect to touch."
        ),
        annotations=write,
        structured_output=True,
    )
    def forge_lease_acquire(
        project: str,
        row_id: str,
        holder: str = "agent",
        scope_files: list[str] | None = None,
        ttl_s: int = DEFAULT_TTL,
    ) -> AcquireOut:
        why = check(project, row_id)
        if why:
            raise ToolError(f"NOT_READY: {why}")
        lease = guarded(
            lambda: leases.acquire(project, row_id, holder, scope_files, ttl_s)
        )
        return {
            "lease_id": lease.lease_id,
            "row_key": lease.row_key,
            "expires_at": _iso(lease.expires_at),
            "scope_files": lease.scope_files,
        }

    @app.tool(
        description="Extend a live lease by its TTL. An expired lease is an error: LEASE_EXPIRED.",
        annotations=write,
        structured_output=True,
    )
    def forge_lease_heartbeat(lease_id: str) -> HeartbeatOut:
        return {
            "lease_id": lease_id,
            "expires_at": _iso(guarded(lambda: leases.heartbeat(lease_id))),
        }

    @app.tool(
        description="End a lease: outcome handoff, pr_opened (needs pr_url) or abandoned. The row is free again.",
        annotations=write,
        structured_output=True,
    )
    def forge_lease_release(
        lease_id: str, outcome: str, note: str = "", pr_url: str = ""
    ) -> ReleaseOut:
        return {
            "lease_id": lease_id,
            "state": guarded(lambda: leases.release(lease_id, outcome, note, pr_url)),
        }

    @app.tool(
        description=(
            "Mark a leased row blocked: kind dependency, spec_question, decision_needed or env, with what blocks it. "
            "needs_a_person=true means stop: the same block recurred, or a decision is required."
        ),
        annotations=write,
        structured_output=True,
    )
    def forge_block(lease_id: str, kind: str, detail: str) -> BlockOut:
        return guarded(lambda: leases.block(lease_id, kind, detail))  # type: ignore[no-any-return]

    @app.tool(
        description="Live leases and the most recent lease events.",
        annotations=read,
        structured_output=True,
    )
    def forge_leases(row_key: str = "", limit: int = 20) -> LeasesOut:
        return {
            "live": [
                {
                    "lease_id": x.lease_id,
                    "row_key": x.row_key,
                    "holder": x.holder,
                    "expires_at": _iso(x.expires_at),
                    "scope_files": x.scope_files,
                }
                for x in leases.live()
            ],
            "recent_events": leases.events(row_key, max(1, min(limit, 200))),
        }

    return app


def _ready_from_planner(project: str, row_id: str) -> str:
    """Empty if forge_next would list the row, else the reason it is held back."""
    import planner

    try:
        active = planner.active_projects()
    except Exception as error:  # the board could not be read: refuse rather than guess
        return f"could not read Active from the board: {error}"
    result = planner.ready_rows(active, project, limit=10_000)
    ready = result["ready"] if isinstance(result["ready"], list) else []
    skipped = result["skipped"] if isinstance(result["skipped"], dict) else {}
    if any(r["row_id"] == row_id for r in ready):
        return ""
    reasons = ", ".join(f"{k} ({v})" for k, v in skipped.items())
    return f"{project}:{row_id} is not ready" + (
        f": {reasons}" if reasons else ": not an open row of that project"
    )


def main(argv: list[str]) -> int:
    if not argv:
        import anyio

        anyio.run(build().run_stdio_async)
        return 0
    store = Store()
    if argv[0] == "list":
        for lease in store.live():
            print(
                f"{lease.row_key}  {lease.holder}  until {_iso(lease.expires_at)}  {lease.lease_id}"
            )
        return 0
    if argv[0] == "steal" and len(argv) >= 2:
        print(store.steal(argv[1], " ".join(argv[2:]) or "stolen by a person"))
        return 0
    print("usage: leases.py [list | steal <lease_id> [reason]]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
