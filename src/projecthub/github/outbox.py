"""Drain the local outbox onto the organization GitHub Project: paced, idempotent, and deferential to people.

The work tools write board updates (claims, blocks, activity, proposals) to an `outbox` table in the local
SQLite store, and return at once. This module is the one place those updates leave the machine. It reads the
Project once per run, resolves each queued row to its item, and writes the fields in batched mutations.

Rules, each one an owner decision:

- **Status precedence.** projecthub writes Status only at its own events. It remembers, per item, the last
  Status it wrote (`outbox_state`). When the live Status differs from projecthub's last write, a person moved
  the card after it: projecthub leaves Status alone and records a conflict (`outbox_conflicts`).
- **No Done.** Only an evidence verdict may set Done, and that is not this module. A queued Done is dropped,
  and a card a person moved to Done is never moved away from it.
- **No note text.** Only the fields in `BOARD_FIELDS` are mirrored; anything else in a payload stays local.
- **Proposals** become an issue in the portfolio repository titled `[project] proposal-<id>: <title>`, added
  to the Project in Triage. A public Project shows drafts, so a draft item is never used, and a proposal
  marked private is never published.
- **Nothing is dropped.** A missing item, a transient error or a refused value is retried later with
  backoff; only a write that went through (or a proposal that must stay private) is marked done.
- **Paced.** Every write is counted in `outbox_writes`, persisted, so a second run inside the same hour sees
  the first one's writes. The defaults stay under GitHub's secondary limits (80 content-creating requests a
  minute, 500 an hour), with a daily cap on top. A spent hour or day ends the run; entries not reached stay
  pending, untouched.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sqlite3
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from projecthub import config
from projecthub.github.client import (
    FIELD_BATCH,
    TRANSIENT,
    Change,
    GhProjectsError,
    Item,
    MissingScope,
    Outcome,
    Project,
    RateLimited,
    encode_value,
)
from projecthub.mcp.loop import CARD, plain

# The fields the outbox may write. Anything else in a payload (a note above all) never reaches GitHub.
BOARD_FIELDS = ("Status", "Claimed", "Agent", "Last activity")
NEVER_WRITTEN = "Done"
TRIAGE = "Triage"

# GitHub's documented secondary limits are 80 content-creating requests a minute and 500 an hour. Each field
# write is counted as one, which is the conservative reading for aliased mutations; the defaults leave margin.
PER_MINUTE = 75
PER_HOUR = 450
PER_DAY = 2000
PACE_S = 60 / PER_MINUTE

BACKOFF_BASE_S = 60.0
BACKOFF_MAX_S = 6 * 3600.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox_state (
    item_id TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT,
    at REAL NOT NULL,
    PRIMARY KEY (item_id, field)
);
CREATE TABLE IF NOT EXISTS outbox_conflicts (
    id INTEGER PRIMARY KEY,
    outbox_id INTEGER,
    item_id TEXT NOT NULL,
    field TEXT NOT NULL,
    wanted TEXT,
    live TEXT,
    last TEXT,
    at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox_writes (at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS outbox_writes_at ON outbox_writes (at);
"""


class OutboxStore(Protocol):
    """What the drain needs from the work store's outbox. `pending` returns due, undone entries, oldest first,
    each a dict with id, sink, key, payload (already a dict) and attempts."""

    def pending(self, limit: int) -> list[dict[str, Any]]: ...

    def mark_done(self, id: int) -> None: ...

    def retry_later(self, id: int, delay_s: float) -> None: ...


class BoardClient(Protocol):
    """The slice of `client.Projects` the drain uses."""

    def graphql(
        self, query: str, variables: dict[str, object] | None = None
    ) -> dict[str, Any]: ...

    def project(self, owner: str, number: int) -> Project: ...

    def items(self, owner: str, number: int) -> list[Item]: ...

    def set_values(
        self, project: Project, changes: Sequence[Change]
    ) -> list[Outcome]: ...

    def add_items(
        self, project_id: str, content_ids: Sequence[str]
    ) -> dict[str, str]: ...


class BudgetExhausted(RuntimeError):
    """The hour's or the day's write budget is spent. The run stops instead of waiting."""


class DrainState:
    """projecthub's own memory of what it wrote, what conflicted, and when it wrote: three local tables."""

    def __init__(self, path: pathlib.Path | str = ":memory:") -> None:
        self.db = sqlite3.connect(str(path), timeout=30, isolation_level=None)
        self.db.executescript(SCHEMA)

    def last(self, item_id: str, field_name: str) -> str | None:
        """The value projecthub last wrote to this field of this item, or None when it never wrote one."""
        row = self.db.execute(
            "SELECT value FROM outbox_state WHERE item_id=? AND field=?",
            (item_id, field_name),
        ).fetchone()
        return None if row is None else row[0]

    def record(self, item_id: str, field_name: str, value: str, at: float) -> None:
        self.db.execute(
            "INSERT INTO outbox_state (item_id, field, value, at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (item_id, field) DO UPDATE SET value=excluded.value, at=excluded.at",
            (item_id, field_name, value, at),
        )

    def conflict(
        self,
        outbox_id: int,
        item_id: str,
        field_name: str,
        wanted: str,
        live: str | None,
        last: str | None,
        at: float,
    ) -> None:
        self.db.execute(
            "INSERT INTO outbox_conflicts (outbox_id, item_id, field, wanted, live, last, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (outbox_id, item_id, field_name, wanted, live, last, at),
        )

    def conflicts(self) -> list[dict[str, Any]]:
        cursor = self.db.execute(
            "SELECT outbox_id, item_id, field, wanted, live, last, at FROM outbox_conflicts ORDER BY id"
        )
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def stamps(self, since: float) -> list[float]:
        return [
            r[0]
            for r in self.db.execute(
                "SELECT at FROM outbox_writes WHERE at > ? ORDER BY at", (since,)
            )
        ]

    def wrote(self, at: float, count: int) -> None:
        self.db.executemany(
            "INSERT INTO outbox_writes (at) VALUES (?)", [(at,)] * count
        )
        self.db.execute("DELETE FROM outbox_writes WHERE at <= ?", (at - 86400,))


@dataclass
class Budget:
    """Spaces writes `pace_s` apart and holds them under the minute, hour and day ceilings.

    A minute's ceiling is waited out; an hour's or a day's ends the run, because waiting that long inside one
    run is worse than running again later. Counts are read from `outbox_writes`, so they span runs.
    """

    state: DrainState
    pace_s: float = PACE_S
    per_minute: int = PER_MINUTE
    per_hour: int = PER_HOUR
    per_day: int = PER_DAY
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    next_free: float = 0.0

    def take(self, n: int) -> None:
        """Block until `n` writes may go now, and count them. Raises BudgetExhausted for the hour or day."""
        if n > self.per_minute:
            raise ValueError(
                f"{n} writes in one request exceed {self.per_minute} a minute"
            )
        now = self.clock()
        if now < self.next_free:
            self.sleep(self.next_free - now)
            now = self.clock()
        day = self.state.stamps(now - 86400)
        if len(day) + n > self.per_day:
            raise BudgetExhausted(
                f"{len(day)} writes today; the daily cap is {self.per_day}"
            )
        hour = [t for t in day if t > now - 3600]
        if len(hour) + n > self.per_hour:
            raise BudgetExhausted(
                f"{len(hour)} writes this hour; the cap is {self.per_hour}"
            )
        minute = [t for t in hour if t > now - 60]
        excess = len(minute) + n - self.per_minute
        if excess > 0:
            self.sleep(60 - (now - minute[excess - 1]))
            now = self.clock()
        self.state.wrote(now, n)
        self.next_free = now + self.pace_s * n


@dataclass
class Report:
    done: list[int] = field(default_factory=list)
    retried: list[tuple[int, str]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    writes: int = 0
    stopped: str = ""

    def lines(self) -> list[str]:
        out = [*self.planned]
        out += [f"conflict: {c}" for c in self.conflicts]
        out += [f"skipped: {s}" for s in self.skipped]
        out += [f"retry #{i}: {why}" for i, why in self.retried]
        out.append(
            f"{len(self.done)} done, {len(self.retried)} retried, {len(self.conflicts)} conflicts, "
            f"{self.writes} writes"
        )
        if self.stopped:
            out.append(f"stopped: {self.stopped}")
        return out


def backoff(attempts: int) -> float:
    return min(BACKOFF_BASE_S * 2 ** max(attempts, 0), BACKOFF_MAX_S)


def is_transient(error: BaseException) -> bool:
    return any(t in str(error) for t in TRANSIENT)


def split_key(row_key: str) -> tuple[str, str]:
    project, _, row = row_key.partition(":")
    return project, row


def find_item(items: Sequence[Item], project: str, row: str) -> list[Item]:
    """Items titled `[project] row...`. The prefix decides the project, because row ids repeat across them."""
    found = []
    for item in items:
        match = CARD.match(item.title.strip())
        if (
            match
            and match.group("project").strip() == project
            and plain(match.group("row")) == plain(row)
        ):
            found.append(item)
    return found


def proposal_slug(payload: dict[str, Any]) -> tuple[str, str]:
    """(project, local id) of a proposal: `project` when the payload names it, else the `project:` prefix."""
    pid = str(payload.get("proposal_id", ""))
    project = str(payload.get("project") or "")
    head, sep, tail = pid.partition(":")
    if not project and sep:
        project = head
    local = tail if sep and head == project else pid
    return project, re.sub(r"[^\w.-]", "-", local)


def proposal_title(project: str, slug: str, title: str) -> str:
    return f"[{project}] proposal-{slug}: {title}".rstrip(": ")


def repo_id(client: BoardClient, repo: str) -> str:
    owner, _, name = repo.partition("/")
    data = client.graphql(
        "query($o: String!, $n: String!) { repository(owner: $o, name: $n) { id } }",
        {"o": owner, "n": name},
    )
    found = (data.get("repository") or {}).get("id")
    if not found:
        raise GhProjectsError(f"no repository {repo!r}")
    return str(found)


def create_issue(client: BoardClient, repository_id: str, title: str, body: str) -> str:
    data = client.graphql(
        "mutation($i: CreateIssueInput!) { createIssue(input: $i) { issue { id url } } }",
        {"i": {"repositoryId": repository_id, "title": title, "body": body}},
    )
    issue = (data.get("createIssue") or {}).get("issue") or {}
    if not issue.get("id"):
        raise GhProjectsError(f"creating {title!r} returned no issue")
    return str(issue["id"])


@dataclass
class _Pending:
    entry: dict[str, Any]
    item: Item
    changes: list[Change]


class Drain:
    def __init__(
        self,
        store: OutboxStore,
        client: BoardClient,
        state: DrainState,
        budget: Budget,
        *,
        owner: str,
        number: int,
        repo: str,
        dry_run: bool,
    ) -> None:
        self.store = store
        self.client = client
        self.state = state
        self.budget = budget
        self.owner = owner
        self.number = number
        self.repo = repo
        self.dry_run = dry_run
        self.report = Report()
        self.project: Project | None = None
        self.items: list[Item] = []
        # Field values as this run believes them now: the snapshot, plus every write that went through.
        self.live: dict[str, dict[str, object]] = {}
        self.batch: list[_Pending] = []
        self._repo_id = ""

    # -- bookkeeping -------------------------------------------------------------------------------

    def done(self, entry: dict[str, Any]) -> None:
        if not self.dry_run:
            self.store.mark_done(int(entry["id"]))
        self.report.done.append(int(entry["id"]))

    def retry(self, entry: dict[str, Any], why: str) -> None:
        if not self.dry_run:
            self.store.retry_later(
                int(entry["id"]), backoff(int(entry.get("attempts") or 0))
            )
        self.report.retried.append((int(entry["id"]), why))

    def spend(self, n: int) -> None:
        if not self.dry_run:
            self.budget.take(n)
        self.report.writes += n

    # -- the run -----------------------------------------------------------------------------------

    def run(self, limit: int) -> Report:
        entries = [e for e in self.store.pending(limit) if e.get("sink") == "board"]
        if not entries:
            return self.report
        self.project = self.client.project(self.owner, self.number)
        self.items = self.client.items(self.owner, self.number)
        self.live = {i.id: dict(i.values) for i in self.items}
        try:
            for entry in entries:
                payload = entry.get("payload") or {}
                if "proposal_id" in payload:
                    self.flush()
                    self.proposal(entry, payload)
                else:
                    self.row(entry, payload)
            self.flush()
        except (BudgetExhausted, MissingScope, RateLimited) as error:
            self.report.stopped = str(error)
        return self.report

    def fields_of(
        self, entry: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, object]:
        wanted: dict[str, object] = {}
        for name, value in (payload.get("fields") or {}).items():
            if name not in BOARD_FIELDS:
                self.report.skipped.append(f"#{entry['id']} {name}: not mirrored")
            elif name == "Status" and value == NEVER_WRITTEN:
                self.report.skipped.append(
                    f"#{entry['id']} Status Done: only evidence sets Done"
                )
            elif name == "Status" and value is None:
                continue
            else:
                wanted[name] = value
        return wanted

    def changes_for(
        self, entry: dict[str, Any], item: Item, wanted: dict[str, object]
    ) -> list[Change]:
        """The writes that are still needed, with Status deferring to a person who moved the card."""
        assert self.project is not None
        live = self.live.setdefault(item.id, {})
        out: list[Change] = []
        for name, value in wanted.items():
            if name not in self.project.fields:
                raise GhProjectsError(f"project has no field {name!r}")
            if value is not None:
                encode_value(
                    self.project.fields[name], value
                )  # an unknown option raises here
            current = live.get(name)
            if name == "Status":
                last = self.state.last(item.id, "Status")
                moved = (last is not None and current != last) or (
                    last is None and current == NEVER_WRITTEN
                )
                if moved:
                    if not self.dry_run:
                        self.state.conflict(
                            int(entry["id"]),
                            item.id,
                            "Status",
                            str(value),
                            None if current is None else str(current),
                            last,
                            self.budget.clock(),
                        )
                    self.report.conflicts.append(
                        f"#{entry['id']} {item.title}: Status is {current!r} and projecthub's last "
                        f"write was {last!r}; left it, wanted {value!r}"
                    )
                    continue
                if current == value:
                    if not self.dry_run:
                        self.state.record(
                            item.id, "Status", str(value), self.budget.clock()
                        )
                    continue
            elif current == value:
                continue
            out.append(Change(item.id, name, value))
        return out

    def row(self, entry: dict[str, Any], payload: dict[str, Any]) -> None:
        project, row = split_key(str(payload.get("row_key") or entry.get("key") or ""))
        found = find_item(self.items, project, row)
        if len(found) != 1:
            why = "no item" if not found else f"{len(found)} items"
            self.retry(entry, f"{why} titled [{project}] {row}")
            return
        item = found[0]
        if item.truncated and "Status" not in item.values:
            self.retry(entry, f"{item.title}: field values truncated, Status unknown")
            return
        try:
            changes = self.changes_for(entry, item, self.fields_of(entry, payload))
        except GhProjectsError as error:
            self.retry(entry, str(error))
            return
        if not changes:
            self.done(entry)
            return
        if any(p.item.id == item.id for p in self.batch) or (
            sum(len(p.changes) for p in self.batch) + len(changes) > FIELD_BATCH
        ):
            self.flush()
        self.batch.append(_Pending(entry, item, changes))

    def flush(self) -> None:
        batch, self.batch = self.batch, []
        if not batch:
            return
        assert self.project is not None
        changes = [c for p in batch for c in p.changes]
        if self.dry_run:
            for p in batch:
                for c in p.changes:
                    self.report.planned.append(
                        f"would set {p.item.title!r} {c.field_name} = {c.value!r}"
                    )
                self.done(p.entry)
            self.report.writes += len(changes)
            return
        self.spend(len(changes))
        try:
            outcomes = self.client.set_values(self.project, changes)
        except (MissingScope, RateLimited):
            for p in batch:
                self.retry(p.entry, "run stopped before this write")
            raise
        except GhProjectsError as error:
            for p in batch:
                self.retry(
                    p.entry, ("transient: " if is_transient(error) else "") + str(error)
                )
            return
        failed: dict[str, str] = {}
        for outcome in outcomes:
            c = outcome.change
            if outcome.ok:
                self.live.setdefault(c.item_id, {})[c.field_name] = c.value
                if c.field_name == "Status":
                    self.state.record(
                        c.item_id, "Status", str(c.value), self.budget.clock()
                    )
            else:
                failed.setdefault(c.item_id, f"{c.field_name}: {outcome.error}")
        for p in batch:
            if p.item.id in failed:
                self.retry(p.entry, failed[p.item.id])
            else:
                self.done(p.entry)

    def proposal(self, entry: dict[str, Any], payload: dict[str, Any]) -> None:
        """An issue in the portfolio repository, added to the Project in Triage. Never a draft."""
        assert self.project is not None
        if payload.get("private"):
            self.report.skipped.append(
                f"#{entry['id']} proposal is private; not published"
            )
            self.done(entry)
            return
        project, slug = proposal_slug(payload)
        if not project or not slug:
            self.retry(entry, "proposal names no project")
            return
        title = proposal_title(project, slug, str(payload.get("title") or ""))
        key = f"proposal:{project}:{slug}"
        prefix = f"[{project}] proposal-{slug}"
        on_board = [
            i
            for i in self.items
            if i.title == prefix or i.title.startswith(prefix + ":")
        ]
        if self.dry_run:
            if not on_board:
                self.report.planned.append(
                    f"would create issue {title!r} in {self.repo}"
                )
                self.report.planned.append(
                    f"would add it to the Project with Status {TRIAGE}"
                )
                self.report.writes += 3
            self.done(entry)
            return
        try:
            if on_board:
                item = on_board[0]
            else:
                content_id = self.state.last(key, "issue")
                if content_id is None:
                    if not self._repo_id:
                        self._repo_id = repo_id(self.client, self.repo)
                    self.spend(1)
                    kind = str(payload.get("kind") or "proposal")
                    body = (
                        f"A {kind} proposed through projecthub, waiting for triage.\n\n"
                        f"Proposal id: `{payload.get('proposal_id')}`"
                    )
                    content_id = create_issue(self.client, self._repo_id, title, body)
                    self.state.record(key, "issue", content_id, self.budget.clock())
                self.spend(1)
                added = self.client.add_items(self.project.id, [content_id])
                if content_id not in added:
                    self.retry(entry, f"{title!r} was not added to the Project")
                    return
                item = Item(added[content_id], "ISSUE", content_id, "", title, {})
                self.items.append(item)
                self.live[item.id] = {}
            if self.live.get(item.id, {}).get("Status") is None:
                self.spend(1)
                outcomes = self.client.set_values(
                    self.project, [Change(item.id, "Status", TRIAGE)]
                )
                bad = [o for o in outcomes if not o.ok]
                if bad:
                    self.retry(entry, f"Status {TRIAGE}: {bad[0].error}")
                    return
                self.live.setdefault(item.id, {})["Status"] = TRIAGE
                self.state.record(item.id, "Status", TRIAGE, self.budget.clock())
        except (MissingScope, RateLimited, BudgetExhausted):
            raise
        except GhProjectsError as error:
            self.retry(
                entry, ("transient: " if is_transient(error) else "") + str(error)
            )
            return
        self.done(entry)


def drain(
    store: OutboxStore,
    client: BoardClient,
    limit: int = 50,
    pace_s: float = PACE_S,
    *,
    state: DrainState | None = None,
    owner: str = config.PROJECT_OWNER,
    number: int = config.PROJECT_NUMBER,
    repo: str = config.PORTFOLIO_REPO,
    per_minute: int = PER_MINUTE,
    per_hour: int = PER_HOUR,
    per_day: int = PER_DAY,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    dry_run: bool = False,
) -> Report:
    """Send up to `limit` due board entries. The state tables live beside the store's own when it has a path."""
    if per_minute > 80 or per_hour > 500:
        raise ValueError("per_minute and per_hour must stay within GitHub's 80 and 500")
    if state is None:
        path = getattr(store, "path", None)
        state = DrainState(path if path is not None else ":memory:")
    budget = Budget(state, pace_s, per_minute, per_hour, per_day, clock, sleep)
    return Drain(
        store,
        client,
        state,
        budget,
        owner=owner,
        number=number,
        repo=repo,
        dry_run=dry_run,
    ).run(limit)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub drain",
        description="send queued board updates from the local outbox to the organization GitHub Project",
    )
    parser.add_argument(
        "--limit", type=int, default=50, help="entries to send this run"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the writes without making them"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    from projecthub.github.client import Projects
    from projecthub.mcp.work import Store

    store = Store()
    if not all(hasattr(store, m) for m in ("pending", "mark_done", "retry_later")):
        print("the work store has no outbox yet; nothing to drain", file=sys.stderr)
        return 2
    try:
        report = drain(
            cast(OutboxStore, store), Projects(), limit=args.limit, dry_run=args.dry_run
        )
    except GhProjectsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for line in report.lines():
        print(line)
    return 1 if report.stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
