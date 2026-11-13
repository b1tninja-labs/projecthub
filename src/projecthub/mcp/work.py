"""Leases: who is working on which ROADMAP row right now, minted by the server and expiring on their own.

The authority is GitHub Project 1. A claim is decided there, synchronously, by github/claims.py: Agent,
Claimed, Status and Claim expires are written, the item is re-read, and the claim stands only if it still
names this lease. This file keeps the local half: a mirror of each won claim (so the lease id stays the
capability every other tool takes, and those tools keep working offline), the event log whose text never
leaves the machine, and the outbox that carries every later board write. With no network a claim is refused,
never faked.

Why not the GitHub assignee. Every agent session here acts as the same account, so `claim()` could not
tell two agents apart: the second one read "already yours". A lease is a server-minted opaque handle
(UUIDv4) with a time to live. Holding the handle is the capability: heartbeat, release and block all
take it, and a lease that misses its heartbeat expires by itself, so a crashed agent cannot hold a row
forever. The assignee and issue comments may mirror a lease for people to see; they never decide it.

Where it lives. A SQLite file in the machine's state directory (paths.state_dir), acquired inside BEGIN IMMEDIATE so two
agents racing for one row get exactly one lease. It writes nothing else: not ROADMAP.md, not GitHub.
That is why this is its own opt-in MCP server (`projecthub-work`) rather than tools on the read-only planner:
a client that registers only the planner cannot reach a write of any kind.

State: live -> released (handoff / pr_opened / abandoned / parked) | expired | blocked. A block of the same
kind on the same row more than twice escalates: the row needs a person, not another attempt (the Hermes rule).
A holder has one live lease at a time unless the caller asks for more.

The work log. Beside the lease, an agent can record a note (progress, finding, test_written, pr_opened), park
a row with a resume note, propose new work (it lands in a local `proposals` table and on the board's Triage
status, where the owner accepts or rejects it) and submit a commit for an evidence check (a `submissions` row
in state 'queued'; this module runs no tests). None of these marks anything done or writes a ROADMAP: done is
decided only by an evidence check.

The outbox. Every event that changes what the board shows also writes an `outbox` row in the same transaction,
coalesced per (sink, key), so a crash cannot leave an event without its pending write or the reverse, and ten
updates to one item stay one pending write. Another process drains it with `pending`, `mark_done` and
`retry_later`. A payload carries a status, a date, the holder and "<event kind> <ISO time>": note, block and
release text stays in this file and never leaves the machine.

Usage:
    python -m projecthub.mcp.work                 # speaks MCP on stdio
    python -m projecthub.mcp.work list            # live leases, for a person
    python -m projecthub.mcp.work steal <lease_id>  # human-only: end someone else's lease
    python -m projecthub.mcp.work note | park | propose | submit | log | outbox ...   # see --help
"""

from __future__ import annotations

from projecthub import paths

import argparse
import json
import pathlib
import re
import sqlite3
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from projecthub.github.claims import ProjectClaims

DEFAULT_DB = paths.state_dir() / "leases.sqlite"


def default_db() -> pathlib.Path:
    """The store file every process on this machine shares: PROJECTHUB_LEASES_DB, else the state directory."""
    return pathlib.Path(paths.env("LEASES_DB") or DEFAULT_DB)


DEFAULT_TTL = 30 * 60
MAX_TTL = 4 * 60 * 60
OUTCOMES = ("handoff", "pr_opened", "abandoned", "parked")
BLOCK_KINDS = ("dependency", "spec_question", "decision_needed", "env")
NOTE_KINDS = ("progress", "finding", "test_written", "pr_opened")
PROPOSAL_KINDS = ("obligation", "bug", "task", "question")
SINKS = ("board", "site")
ESCALATE_AFTER = 2
SHA = re.compile(r"[0-9a-f]{40}")

CLAIM_EXPIRES = "Claim expires"
# What the board shows for each release outcome. Every event also sets "Last activity".
RELEASE_STATUS = {
    "handoff": "Ready",
    "abandoned": "Ready",
    "parked": "Ready",
    "pr_opened": "In review",
}
# The outcomes that keep the holder's name on the item: the work is still theirs, waiting on review.
KEEPS_AGENT = ("pr_opened",)
# The event kinds whose text the next holder of a row should read first, beside every note:* kind.
HANDOFF_KINDS = ("release:handoff", "release:parked")

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
    pr_url TEXT,
    branch TEXT
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
CREATE INDEX IF NOT EXISTS events_row ON events(row_key, id);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY,
    sink TEXT NOT NULL CHECK (sink IN ('board', 'site')),
    key TEXT NOT NULL,
    payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_at REAL NOT NULL DEFAULT 0,
    done_at REAL,
    UNIQUE(sink, key)
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY,
    project TEXT NOT NULL,
    title TEXT NOT NULL,
    why TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('obligation', 'bug', 'task', 'question')),
    spec TEXT NOT NULL DEFAULT '',
    found_in TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'accepted', 'rejected')),
    private INTEGER NOT NULL DEFAULT 0,
    proposed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_key TEXT NOT NULL,
    sha TEXT NOT NULL,
    lease_id TEXT,
    at REAL NOT NULL,
    state TEXT NOT NULL
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
    branch: str = ""


def row_key(project: str, row_id: str) -> str:
    return f"{project}:{row_id}"


class Store:
    def __init__(
        self, path: pathlib.Path | None = None, now: Callable[[], float] = time.time
    ) -> None:
        self.path = path or default_db()
        self.now = now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)
        with self._write() as db:
            # A store made before notes carried refs, or leases a branch, gains the column once.
            for table, column in (("events", "refs"), ("leases", "branch")):
                columns = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

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
        ).fetchall():
            self._event(
                db,
                row["lease_id"],
                row["row_key"],
                "expire",
                "no heartbeat within the TTL",
                {"Status": "Ready", "Agent": None, CLAIM_EXPIRES: None},
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
        fields: dict[str, str | None] | None = None,
        refs: list[str] | None = None,
        queue: bool = True,
    ) -> None:
        """Append an event and, in the same transaction, its board write: `fields` plus Last activity.

        `detail` stays in this file. The board payload is built only from `fields`, which callers fill with
        statuses, dates and holders, and from the event kind and time. A field set to None asks the drain
        to clear it. An event under a lease carries that claim's token as `claim`, and the drain writes it only
        to an item whose Agent is empty or is that token, so a claim lost on the Project is never overwritten.
        """
        at = self.now()
        db.execute(
            "INSERT INTO events(at, lease_id, row_key, kind, detail, refs) VALUES (?, ?, ?, ?, ?, ?)",
            (at, lease_id, key, kind, detail, json.dumps(refs) if refs else None),
        )
        if not queue:
            return
        board = {**(fields or {}), "Last activity": f"{kind} {_iso(at)}"}
        payload: dict[str, object] = {"row_key": key, "fields": board}
        if lease_id:
            held = db.execute(
                "SELECT holder FROM leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            if held is not None:
                payload["claim"] = claim_token(held["holder"], lease_id)
        self.enqueue(db, "board", key, payload)

    def record(self, lease_id: str | None, key: str, kind: str, detail: str) -> None:
        """A local event with no board write: what happened here that the Project must not hear from us."""
        with self._write() as db:
            self._event(db, lease_id, key, kind, detail, queue=False)

    def lease(self, lease_id: str) -> Lease | None:
        db = self._connect()
        try:
            r = db.execute(
                "SELECT * FROM leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
        finally:
            db.close()
        if r is None:
            return None
        return Lease(
            r["lease_id"],
            r["row_key"],
            r["holder"],
            json.loads(r["scope_files"]),
            r["acquired_at"],
            r["expires_at"],
            r["state"],
            r["branch"] or "",
        )

    def ended_here(self, token: str) -> bool:
        """True when `token` names a lease this store holds and has already ended: the Project still shows
        the claim only because the outbox has not drained yet."""
        holder, sep, tail = token.rpartition(" [")
        if not sep or not tail.endswith("]"):
            return False
        prefix = tail[:-1]
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT state FROM leases WHERE holder=? AND substr(lease_id, 1, ?)=?",
                (holder, len(prefix), prefix),
            ).fetchall()
        finally:
            db.close()
        return bool(rows) and all(r["state"] != "live" for r in rows)

    def lose(self, lease_id: str, rival: str) -> None:
        """End a mirror lease whose claim another holder now has on the Project. Nothing is queued: the item
        is theirs, and a write from here would undo their claim."""
        with self._write() as db:
            found = db.execute(
                "SELECT row_key FROM leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            if found is None:
                raise LeaseError("LEASE_UNKNOWN", f"no lease {lease_id}")
            db.execute(
                "UPDATE leases SET state='released', outcome='lost', note=? WHERE lease_id=?",
                (f"claimed by {rival}", lease_id),
            )
            self._event(db, lease_id, found["row_key"], "lost", rival, queue=False)

    def enqueue(
        self, db: sqlite3.Connection, sink: str, key: str, payload: dict[str, object]
    ) -> None:
        """Internal: queue a write to `sink` inside the caller's transaction, coalesced per (sink, key).

        A pending write for the same item absorbs the new one: top-level keys are replaced and "fields" are
        merged, so a claim followed by a note is still one write that carries Claimed and the latest activity.
        A write that was already drained is replaced, and the attempt count starts over.
        """
        if sink not in SINKS:
            raise LeaseError("BAD_SINK", f"sink must be one of {', '.join(SINKS)}")
        found = db.execute(
            "SELECT payload, done_at FROM outbox WHERE sink=? AND key=?", (sink, key)
        ).fetchone()
        merged = dict(payload)
        if found is not None and found["done_at"] is None:
            before = json.loads(found["payload"])
            merged = {**before, **payload}
            old, new = before.get("fields"), payload.get("fields")
            if isinstance(old, dict) and isinstance(new, dict):
                merged["fields"] = {**old, **new}
        db.execute(
            "INSERT INTO outbox(sink, key, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(sink, key) DO UPDATE SET payload=excluded.payload, attempts=0, next_at=0, "
            "done_at=NULL",
            (sink, key, json.dumps(merged, sort_keys=True)),
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
        max_live: int = 1,
        branch: str = "",
        *,
        lease_id: str | None = None,
        mirror: bool = False,
        acquired_at: float | None = None,
    ) -> Lease:
        """A new lease on a row with no live lease. Readiness is the caller's check; this one is exclusivity.

        A holder may hold `max_live` live leases at once, one by default, so an agent finishes, parks or
        blocks a row before it takes the next. `branch` names the branch the work lands on: the evidence worker accepts
        a submitted commit only if it is reachable from main, master or this branch.

        `mirror=True` is the claim protocol's call (github/claims.py): the Project already shows this claim, so
        nothing is queued, the exclusivity and busy checks were made on the Project, and a stale local lease on
        the row is superseded rather than obeyed. Without it this is a local-only lease, kept for the store's
        own tests and tools; no tool claims that way any more.
        """
        key = row_key(project, row_id)
        span = max(60.0, min(float(ttl), MAX_TTL))
        with self._write() as db:
            self._expire(db)
            held = db.execute(
                "SELECT lease_id, holder, expires_at FROM leases WHERE row_key=? AND state='live'",
                (key,),
            ).fetchone()
            if held is not None and mirror:
                db.execute(
                    "UPDATE leases SET state='expired' WHERE lease_id=?",
                    (held["lease_id"],),
                )
                self._event(
                    db,
                    held["lease_id"],
                    key,
                    "superseded",
                    "the Project shows a newer claim",
                    queue=False,
                )
            elif held is not None:
                raise LeaseError(
                    "LEASE_HELD",
                    f"{key} is leased by {held['holder']} until {_iso(held['expires_at'])}",
                )
            mine = db.execute(
                "SELECT row_key FROM leases WHERE holder=? AND state='live' ORDER BY row_key",
                (holder,),
            ).fetchall()
            if not mirror and len(mine) >= max(1, max_live):
                raise LeaseError(
                    "HOLDER_BUSY",
                    f"{holder} already holds {', '.join(r['row_key'] for r in mine)}; "
                    "release, park or block it first",
                )
            now = self.now() if acquired_at is None else acquired_at
            lease = Lease(
                lease_id or str(uuid.uuid4()),
                key,
                holder,
                sorted(scope_files or []),
                now,
                now + span,
                "live",
                branch.strip(),
            )
            db.execute(
                "INSERT INTO leases(lease_id, row_key, holder, scope_files, ttl, acquired_at, expires_at, "
                "state, branch) VALUES (?, ?, ?, ?, ?, ?, ?, 'live', ?)",
                (
                    lease.lease_id,
                    key,
                    holder,
                    json.dumps(lease.scope_files),
                    span,
                    now,
                    lease.expires_at,
                    lease.branch or None,
                ),
            )
            self._event(
                db,
                lease.lease_id,
                key,
                "acquire",
                f"{holder}, ttl {int(span)}s",
                {
                    "Status": "In progress",
                    "Claimed": time.strftime("%Y-%m-%d", time.gmtime(now)),
                    "Agent": claim_token(holder, lease.lease_id),
                    CLAIM_EXPIRES: _iso(lease.expires_at),
                },
                queue=not mirror,
            )
            return lease

    def heartbeat(self, lease_id: str) -> float:
        """Extend a live lease by its TTL and queue the new Claim expires for the Project."""
        with self._write() as db:
            found = self._live(db, lease_id)
            expires = self.now() + found["ttl"]
            db.execute(
                "UPDATE leases SET expires_at=? WHERE lease_id=?", (expires, lease_id)
            )
            self._event(
                db,
                lease_id,
                found["row_key"],
                "heartbeat",
                "",
                {
                    "Agent": claim_token(found["holder"], lease_id),
                    CLAIM_EXPIRES: _iso(expires),
                },
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
        if outcome == "parked" and not note.strip():
            raise LeaseError(
                "NOTE_REQUIRED",
                "a parked release says where to resume, so the next holder starts there",
            )
        with self._write() as db:
            found = self._live(db, lease_id)
            db.execute(
                "UPDATE leases SET state='released', outcome=?, note=?, pr_url=? WHERE lease_id=?",
                (outcome, note, pr_url or None, lease_id),
            )
            self._event(
                db,
                lease_id,
                found["row_key"],
                f"release:{outcome}",
                note or pr_url,
                release_fields(outcome),
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
            self._event(
                db,
                lease_id,
                key,
                f"block:{kind}",
                detail,
                {"Status": "Blocked", CLAIM_EXPIRES: None},
            )
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
            self._event(
                db,
                lease_id,
                found["row_key"],
                "steal",
                reason,
                {"Status": "Ready", "Agent": None, CLAIM_EXPIRES: None},
            )
            return "released"

    def note(
        self, lease_id: str, kind: str, text: str, refs: list[str] | None = None
    ) -> dict[str, object]:
        """Log a note against the row a live lease holds, and extend the lease by its TTL.

        The text and refs stay in this store; the board learns only that a note of this kind happened.
        """
        if kind not in NOTE_KINDS:
            raise LeaseError("BAD_KIND", f"kind must be one of {', '.join(NOTE_KINDS)}")
        if not text.strip():
            raise LeaseError("TEXT_REQUIRED", "a note says something")
        with self._write() as db:
            found = self._live(db, lease_id)
            expires = self.now() + found["ttl"]
            db.execute(
                "UPDATE leases SET expires_at=? WHERE lease_id=?", (expires, lease_id)
            )
            self._event(
                db,
                lease_id,
                found["row_key"],
                f"note:{kind}",
                text,
                {
                    "Agent": claim_token(found["holder"], lease_id),
                    CLAIM_EXPIRES: _iso(expires),
                },
                refs=refs or [],
            )
            return {
                "lease_id": lease_id,
                "row_key": found["row_key"],
                "kind": kind,
                "expires_at": _iso(expires),
            }

    def park(self, lease_id: str, note: str) -> str:
        """A voluntary pause: the row is free again, and the resume note is the first thing its next holder sees."""
        return self.release(lease_id, "parked", note=note)

    def propose(
        self,
        project: str,
        title: str,
        why: str,
        kind: str,
        spec: str = "",
        found_in: str = "",
        private: bool = False,
    ) -> dict[str, object]:
        """Record proposed work in state open and queue it for the board's Triage status. The owner accepts it.

        The id is "<project>:<n>". A private proposal carries `"private": true`, so the drain keeps it off
        anything published.
        """
        if kind not in PROPOSAL_KINDS:
            raise LeaseError(
                "BAD_KIND", f"kind must be one of {', '.join(PROPOSAL_KINDS)}"
            )
        if not project.strip() or not title.strip():
            raise LeaseError("TITLE_REQUIRED", "a proposal names its project and title")
        if not why.strip():
            raise LeaseError(
                "WHY_REQUIRED", "say why the work is needed, so the owner can triage it"
            )
        with self._write() as db:
            cursor = db.execute(
                "INSERT INTO proposals(project, title, why, kind, spec, found_in, state, private, "
                "proposed_at) VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (project, title, why, kind, spec, found_in, int(private), self.now()),
            )
            proposal_id = f"{project}:{cursor.lastrowid}"
            payload: dict[str, object] = {
                "proposal_id": proposal_id,
                "project": project,
                "fields": {"Status": "Triage"},
                "title": title,
                "kind": kind,
            }
            if private:
                payload["private"] = True
            self.enqueue(db, "board", f"proposal:{proposal_id}", payload)
            return {"proposal_id": proposal_id, "state": "open", "status": "Triage"}

    def submit(self, lease_id: str, sha: str) -> dict[str, object]:
        """Queue a commit for an evidence check and show the row In review. Nothing runs here.

        The evidence worker reads `queued_submissions()` and decides; a submission never marks a row done.
        """
        commit = sha.strip().lower()
        if not SHA.fullmatch(commit):
            raise LeaseError(
                "BAD_SHA", "submit a full 40-hex commit id, not a branch, tag or prefix"
            )
        with self._write() as db:
            found = self._live(db, lease_id)
            key = found["row_key"]
            cursor = db.execute(
                "INSERT INTO submissions(row_key, sha, lease_id, at, state) VALUES (?, ?, ?, ?, 'queued')",
                (key, commit, lease_id, self.now()),
            )
            self._event(db, lease_id, key, "submit", commit, {"Status": "In review"})
            return {
                "submission_id": int(cursor.lastrowid or 0),
                "row_key": key,
                "sha": commit,
                "state": "queued",
            }

    # -- the outbox, for the process that drains it ------------------------------------------------

    def pending(self, limit: int = 50) -> list[dict[str, object]]:
        """Writes not yet done whose retry time has come, oldest first."""
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT * FROM outbox WHERE done_at IS NULL AND next_at <= ? ORDER BY id LIMIT ?",
                (self.now(), max(1, limit)),
            ).fetchall()
        finally:
            db.close()
        return [
            {
                "id": r["id"],
                "sink": r["sink"],
                "key": r["key"],
                "payload": json.loads(r["payload"]),
                "attempts": r["attempts"],
                "next_at": r["next_at"],
            }
            for r in rows
        ]

    def mark_done(
        self, outbox_id: int, payload: dict[str, object] | None = None
    ) -> bool:
        """Mark a write delivered. Pass the payload you delivered, and a write that was coalesced with a newer
        update after `pending()` read it stays pending instead of being lost. False if nothing was marked."""
        with self._write() as db:
            query = "UPDATE outbox SET done_at=? WHERE id=? AND done_at IS NULL"
            params: tuple[object, ...] = (self.now(), outbox_id)
            if payload is not None:
                query += " AND payload=?"
                params += (json.dumps(payload, sort_keys=True),)
            return db.execute(query, params).rowcount == 1

    def retry_later(self, outbox_id: int, delay_s: float) -> None:
        """A delivery failed: count the attempt and wait `delay_s` before offering the write again."""
        with self._write() as db:
            db.execute(
                "UPDATE outbox SET attempts=attempts+1, next_at=? WHERE id=? AND done_at IS NULL",
                (self.now() + max(0.0, float(delay_s)), outbox_id),
            )

    def queued_submissions(self, limit: int = 50) -> list[dict[str, object]]:
        """The evidence worker's hook: submissions waiting for a check, oldest first."""
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT * FROM submissions WHERE state='queued' ORDER BY id LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        finally:
            db.close()
        return [
            {
                "id": r["id"],
                "row_key": r["row_key"],
                "sha": r["sha"],
                "lease_id": r["lease_id"],
                "at": _iso(r["at"]),
                "state": r["state"],
            }
            for r in rows
        ]

    # -- reads -------------------------------------------------------------------------------------

    def log(self, key: str, limit: int = 20) -> list[dict[str, object]]:
        """Recent events for one row, newest first, with their text and refs: this is the local view."""
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT * FROM events WHERE row_key=? ORDER BY id DESC LIMIT ?",
                (key, max(1, min(limit, 200))),
            ).fetchall()
        finally:
            db.close()
        return [
            {
                "at": _iso(r["at"]),
                "lease_id": r["lease_id"],
                "kind": r["kind"],
                "text": r["detail"],
                "refs": json.loads(r["refs"]) if r["refs"] else [],
            }
            for r in rows
        ]

    def last_handoff(self, key: str) -> dict[str, object] | None:
        """The latest handoff, parked or note text on a row, for the packet the next holder reads first."""
        db = self._connect()
        try:
            found = db.execute(
                "SELECT * FROM events WHERE row_key=? AND detail != '' "
                "AND (kind IN (?, ?) OR kind LIKE 'note:%') ORDER BY id DESC LIMIT 1",
                (key, *HANDOFF_KINDS),
            ).fetchone()
        finally:
            db.close()
        if found is None:
            return None
        return {
            "at": _iso(found["at"]),
            "lease_id": found["lease_id"],
            "kind": found["kind"],
            "text": found["detail"],
            "refs": json.loads(found["refs"]) if found["refs"] else [],
        }

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
                r["branch"] or "",
            )
            for r in rows
        ]

    def live_keys(self) -> set[str]:
        return {lease.row_key for lease in self.live()}

    def blocked_keys(self) -> set[str]:
        """Rows whose latest claim ended blocked: they wait for a person, not for the next arrival."""
        with self._write() as db:
            found = db.execute(
                "SELECT row_key FROM leases AS l WHERE state='blocked' AND acquired_at="
                "(SELECT MAX(acquired_at) FROM leases WHERE row_key=l.row_key)"
            ).fetchall()
        return {row["row_key"] for row in found}

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


def claim_token(holder: str, lease_id: str) -> str:
    """What a claim writes to Agent: the holder's name and the start of its lease id (github/claims.py)."""
    return f"{holder} [{lease_id[:8]}]"


def release_fields(outcome: str) -> dict[str, str | None]:
    """The Project fields a release writes: its Status, no Claim expires, and no Agent where the work goes back."""
    fields: dict[str, str | None] = {
        "Status": RELEASE_STATUS[outcome],
        CLAIM_EXPIRES: None,
    }
    if outcome not in KEEPS_AGENT:
        fields["Agent"] = None
    return fields


# -- the opt-in MCP server ----------------------------------------------------------------------------


class AcquireOut(TypedDict):
    lease_id: str
    row_key: str
    expires_at: str
    scope_files: list[str]
    branch: str


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


class NoteOut(TypedDict):
    lease_id: str
    row_key: str
    kind: str
    expires_at: str


class ProposeOut(TypedDict):
    proposal_id: str
    state: str
    status: str


class SubmitOut(TypedDict):
    submission_id: int
    row_key: str
    sha: str
    state: str


class LogOut(TypedDict):
    row_key: str
    events: list[dict[str, object]]
    last_handoff: dict[str, object] | None


INSTRUCTIONS = (
    "projecthub work log: claims on GitHub Project 1 items, mirrored as local leases. Claim one before working "
    "a row the planner lists as ready (one live claim per holder); the claim is written to the Project and "
    "re-read, and LEASE_HELD means someone else holds it or won the race. Offline, claim is refused (OFFLINE) "
    f"and every other tool still works. Heartbeat at least every {DEFAULT_TTL // 60} minutes (the default TTL) or "
    "log a note, which also extends it; LEASE_LOST means stop. End it with release (handoff, pr_opened, abandoned), park (with a "
    "resume note) or block. Propose new work you find; it goes to the owner's Triage. Submit a full commit sha "
    "for an evidence check when the work is ready. Nothing here marks a row done or edits ROADMAP.md: done is "
    "decided only by an evidence check. Note text stays on this machine. If the same block recurs, stop and "
    "ask a person."
)


def board_client() -> tuple[object | None, str]:
    """(the GitHub client claims are made through, why there is none). PROJECTHUB_OFFLINE=1 means none."""
    if paths.env("OFFLINE"):
        return None, "PROJECTHUB_OFFLINE is set"
    from projecthub.github.client import Projects

    return Projects(), ""


def claims_for(store: Store) -> ProjectClaims:
    """The Project-backed claimant for `store`, through the default client."""
    from projecthub.github.claims import ProjectClaims

    client, why = board_client()
    return ProjectClaims(store, client, offline_reason=why)  # type: ignore[arg-type]


def build(  # type: ignore[no-untyped-def]
    store: Store | None = None,
    ready: Callable[[str, str], str] | None = None,
    claims: ProjectClaims | None = None,
):
    """The projecthub-work server. `ready(project, row_id)` returns "" if the row may be leased, else why not.
    `claims` decides claims on the Project; by default it is built over `store` and the gh client."""
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    leases = store or Store()
    board = claims or claims_for(leases)
    check = ready or _ready_from_planner
    app = MCPServer(name="projecthub-work", instructions=INSTRUCTIONS)
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
        name="claim",
        description=(
            f"Claim a ready row on GitHub Project 1. TTL defaults to {DEFAULT_TTL}s and is capped at {MAX_TTL}s; "
            "heartbeat before it runs out. Writes Agent, Claimed, Status In progress and Claim expires, re-reads "
            "the item, and returns a lease id only when the claim stood. Refuses NOT_READY (inactive project, plan "
            "directory, unbuilt dependency, declared non-goal), LEASE_HELD (claimed by someone else, or lost the "
            "race), HOLDER_BUSY, OFFLINE (the Project cannot be reached; nothing is claimed) and RATE_BUDGET_LOW. "
            "scope_files is advisory: the files you expect to touch."
        ),
        annotations=write,
        structured_output=True,
    )
    def claim_tool(
        project: str,
        row_id: str,
        holder: str = "agent",
        scope_files: list[str] | None = None,
        ttl_s: int = DEFAULT_TTL,
        branch: str = "",
    ) -> AcquireOut:
        why = check(project, row_id)
        if why:
            raise ToolError(f"NOT_READY: {why}")
        lease = guarded(
            lambda: board.acquire(
                project, row_id, holder, scope_files, ttl_s, branch=branch
            )
        )
        return {
            "lease_id": lease.lease_id,
            "row_key": lease.row_key,
            "expires_at": _iso(lease.expires_at),
            "scope_files": lease.scope_files,
            "branch": lease.branch,
        }

    @app.tool(
        name="heartbeat",
        description=(
            "Extend a live claim by its TTL; the new Claim expires reaches the Project through the outbox. "
            "LEASE_EXPIRED: the lease ran out. LEASE_LOST: the Project shows someone else's claim now; stop."
        ),
        annotations=write,
        structured_output=True,
    )
    def heartbeat_tool(lease_id: str) -> HeartbeatOut:
        return {
            "lease_id": lease_id,
            "expires_at": _iso(guarded(lambda: board.heartbeat(lease_id))),
        }

    @app.tool(
        name="release",
        description=(
            "End a claim: outcome handoff, pr_opened (needs pr_url), abandoned or parked (needs a note). The Project "
            "gets Status Ready (In review for pr_opened) and loses Claim expires; Agent is cleared except on "
            "pr_opened. Queued through the outbox, so it works offline."
        ),
        annotations=write,
        structured_output=True,
    )
    def release_tool(
        lease_id: str, outcome: str, note: str = "", pr_url: str = ""
    ) -> ReleaseOut:
        return {
            "lease_id": lease_id,
            "state": guarded(lambda: leases.release(lease_id, outcome, note, pr_url)),
        }

    @app.tool(
        name="block",
        description=(
            "Mark a claimed row blocked: kind dependency, spec_question, decision_needed or env, with what blocks it. "
            "The Project gets Status Blocked and loses Claim expires; Agent stays, to show who is waiting. "
            "needs_a_person=true means stop: the same block recurred, or a decision is required."
        ),
        annotations=write,
        structured_output=True,
    )
    def block_tool(lease_id: str, kind: str, detail: str) -> BlockOut:
        return guarded(lambda: leases.block(lease_id, kind, detail))  # type: ignore[no-any-return]

    @app.tool(
        name="leases",
        description="This machine's mirror of live claims, and the most recent lease events. The Project is the authority.",
        annotations=read,
        structured_output=True,
    )
    def leases_tool(row_key: str = "", limit: int = 20) -> LeasesOut:
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

    @app.tool(
        description=(
            "Log a note on the row your lease holds: kind progress, finding, test_written or pr_opened, with refs "
            "(files, URLs, commits) if any. Extends the lease like a heartbeat. The text stays local; the board "
            "shows only that a note was logged."
        ),
        annotations=write,
        structured_output=True,
    )
    def note(
        lease_id: str, kind: str, text: str, refs: list[str] | None = None
    ) -> NoteOut:
        return guarded(lambda: leases.note(lease_id, kind, text, refs or []))  # type: ignore[no-any-return]

    @app.tool(
        description=(
            "Pause a row voluntarily: ends the lease and pins the resume note to the row, so the next holder "
            "starts where you stopped. The note is required."
        ),
        annotations=write,
        structured_output=True,
    )
    def park(lease_id: str, note: str) -> ReleaseOut:
        return {
            "lease_id": lease_id,
            "state": guarded(lambda: leases.park(lease_id, note)),
        }

    @app.tool(
        description=(
            "Propose work you found: kind obligation, bug, task or question, with why it is needed, and the spec "
            "clause and the row or file where you found it if known. It goes to the board's Triage status; the "
            "owner accepts or rejects it. It does not create a ROADMAP row. private=true keeps it off anything published."
        ),
        annotations=write,
        structured_output=True,
    )
    def propose(
        project: str,
        title: str,
        why: str,
        kind: str,
        spec: str = "",
        found_in: str = "",
        private: bool = False,
    ) -> ProposeOut:
        return guarded(  # type: ignore[no-any-return]
            lambda: leases.propose(project, title, why, kind, spec, found_in, private)
        )

    @app.tool(
        description=(
            "Submit the row your lease holds for an evidence check at a full 40-hex commit sha. Queues the check "
            "and shows the row In review; it runs no tests and never marks the row done."
        ),
        annotations=write,
        structured_output=True,
    )
    def submit(lease_id: str, sha: str) -> SubmitOut:
        return guarded(lambda: leases.submit(lease_id, sha))  # type: ignore[no-any-return]

    @app.tool(
        description="Recent events on one row (row_key is project:row_id), newest first, with note text and the latest handoff.",
        annotations=read,
        structured_output=True,
    )
    def log(row_key: str, limit: int = 20) -> LogOut:
        return {
            "row_key": row_key,
            "events": leases.log(row_key, limit),
            "last_handoff": leases.last_handoff(row_key),
        }

    return app


def _ready_from_planner(project: str, row_id: str) -> str:
    """Empty if the read server's next would list the row, else the reason it is held back."""
    from projecthub.mcp import read as planner

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
    if argv[0] == "ensure-claim-field":
        return _ensure_claim_field()
    if argv[0] == "steal" and len(argv) >= 2:
        print(store.steal(argv[1], " ".join(argv[2:]) or "stolen by a person"))
        return 0
    if argv[0] in ("release", "block", "heartbeat") and len(argv) >= 2:
        return _finish(store, argv)
    if argv[0] in WORK_COMMANDS:
        return _work(store, argv)
    print(
        "usage: projecthub leases [list | steal <lease_id> [reason] | heartbeat <lease_id> | ensure-claim-field\n"
        "         | acquire <project> <row_id> [--holder H] [--branch B] [--ttl S] [--scope FILE]...\n"
        "         | release <lease_id> <outcome> [--note TEXT] [--pr-url URL]\n"
        "         | block <lease_id> <kind> <detail>\n"
        "         | note <lease_id> <kind> <text> [--ref REF]... | park <lease_id> <note>\n"
        "         | propose <project> <kind> <title> --why TEXT [--spec S] [--found-in F]\n"
        "         | submit <lease_id> <sha> | log <row_key> [--limit N] | outbox [--limit N]]",
        file=sys.stderr,
    )
    return 2


WORK_COMMANDS = ("acquire", "note", "park", "propose", "submit", "log", "outbox")


def _work(store: Store, argv: list[str]) -> int:
    """The work-log calls the MCP server offers, and the outbox for a person, as subcommands."""
    parser = argparse.ArgumentParser(prog="projecthub leases")
    sub = parser.add_subparsers(dest="command", required=True)
    acquire = sub.add_parser("acquire", help="lease a ready row")
    acquire.add_argument("project")
    acquire.add_argument("row_id")
    acquire.add_argument("--holder", default="agent")
    acquire.add_argument("--branch", default="", help="the branch the work lands on")
    acquire.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    acquire.add_argument(
        "--scope", action="append", default=[], help="a file you expect to touch"
    )
    note = sub.add_parser("note", help="log a note on a leased row; extends the lease")
    note.add_argument("lease_id")
    note.add_argument("kind", choices=NOTE_KINDS)
    note.add_argument("text", nargs="+")
    note.add_argument("--ref", action="append", default=[])
    park = sub.add_parser("park", help="pause a row with a resume note")
    park.add_argument("lease_id")
    park.add_argument("note", nargs="+")
    propose = sub.add_parser("propose", help="propose work for the owner's Triage")
    propose.add_argument("project")
    propose.add_argument("kind", choices=PROPOSAL_KINDS)
    propose.add_argument("title", nargs="+")
    propose.add_argument("--why", required=True)
    propose.add_argument("--spec", default="")
    propose.add_argument("--found-in", default="")
    propose.add_argument(
        "--private", action="store_true", help="never publish this proposal"
    )
    submit = sub.add_parser("submit", help="queue a commit for an evidence check")
    submit.add_argument("lease_id")
    submit.add_argument("sha")
    log = sub.add_parser("log", help="recent events on a row, with note text")
    log.add_argument("row_key")
    log.add_argument("--limit", type=int, default=20)
    outbox = sub.add_parser("outbox", help="pending outbox writes")
    outbox.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    try:
        got: object
        if args.command == "acquire":
            why = _ready_from_planner(args.project, args.row_id)
            if why:
                raise LeaseError("NOT_READY", why)
            lease = claims_for(store).acquire(
                args.project,
                args.row_id,
                args.holder,
                args.scope,
                args.ttl,
                branch=args.branch,
            )
            got = {
                "lease_id": lease.lease_id,
                "row_key": lease.row_key,
                "expires_at": _iso(lease.expires_at),
                "scope_files": lease.scope_files,
                "branch": lease.branch,
            }
        elif args.command == "note":
            got = store.note(args.lease_id, args.kind, " ".join(args.text), args.ref)
        elif args.command == "park":
            got = store.park(args.lease_id, " ".join(args.note))
        elif args.command == "propose":
            got = store.propose(
                args.project,
                " ".join(args.title),
                args.why,
                args.kind,
                args.spec,
                args.found_in,
                args.private,
            )
        elif args.command == "submit":
            got = store.submit(args.lease_id, args.sha)
        elif args.command == "log":
            got = {
                "row_key": args.row_key,
                "events": store.log(args.row_key, args.limit),
                "last_handoff": store.last_handoff(args.row_key),
            }
        else:
            got = store.pending(args.limit)
    except LeaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(got if isinstance(got, str) else json.dumps(got, indent=2))
    return 0


def _finish(store: Store, argv: list[str]) -> int:
    """The same calls the work MCP server offers, for an agent whose client has no MCP."""
    command, lease_id, *rest = argv
    try:
        if command == "heartbeat":
            print(f"renewed until {_iso(claims_for(store).heartbeat(lease_id))}")
        elif command == "release":
            parser = argparse.ArgumentParser(prog="projecthub leases release")
            parser.add_argument("outcome")
            parser.add_argument("--note", default="")
            parser.add_argument("--pr-url", default="")
            args = parser.parse_args(rest)
            print(
                store.release(
                    lease_id, args.outcome, note=args.note, pr_url=args.pr_url
                )
            )
        else:
            if len(rest) < 2:
                print(
                    "usage: projecthub leases block <lease_id> <kind> <detail>",
                    file=sys.stderr,
                )
                return 2
            print(json.dumps(store.block(lease_id, rest[0], " ".join(rest[1:]))))
    except LeaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _ensure_claim_field() -> int:
    """A person's one-time step: create the Claim expires field on the Project when it is missing."""
    from projecthub import config
    from projecthub.github.claims import ensure_claim_field
    from projecthub.github.client import GhProjectsError, Projects

    try:
        _, created = ensure_claim_field(Projects())
    except GhProjectsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"{'created' if created else 'already present'}: {CLAIM_EXPIRES!r} on {config.BOARD_URL}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
