"""A short-lived copy of Project 1's items, so a pickup, a next and a claim in the same minute read it once.

The Project is the authority; this is a read cache, never a source. An entry is keyed by (owner, number) and
is fresh for `MAX_AGE_S` seconds. It lives in a table of the local work store (the same SQLite file as the
outbox), so the processes on one machine share it, and a claim that wins patches its own item in place so
the next reader sees the claim without another full read.

One paginated query per refresh: `Projects.items` pages 50 items at a time, one GraphQL point a page (measured
against Project 1 on 2026-11-12, see docs/rate-budget.md). When the rate budget is under its read floor a stale
entry is served rather than nothing, and with no entry at all the read is refused with the budget's reason.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from typing import Protocol

from projecthub.github.client import Item
from projecthub.github.ratebudget import BUDGET, RateBudget

MAX_AGE_S = 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS board_cache (
    owner TEXT NOT NULL,
    number INTEGER NOT NULL,
    fetched_at REAL NOT NULL,
    items TEXT NOT NULL,
    PRIMARY KEY (owner, number)
);
"""


class ItemReader(Protocol):
    def items(self, owner: str, number: int) -> list[Item]: ...


def _default_path() -> pathlib.Path:
    from projecthub.mcp import work

    return work.default_db()


class BoardCache:
    def __init__(
        self,
        path: pathlib.Path | str | None = None,
        *,
        max_age_s: float = MAX_AGE_S,
        clock: Callable[[], float] = time.time,
        budget: RateBudget | None = None,
    ) -> None:
        self.path = str(path) if path is not None else str(_default_path())
        if self.path != ":memory:":
            pathlib.Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.max_age_s = max_age_s
        self.clock = clock
        self.budget = budget or BUDGET
        if self.path == ":memory:":
            # One connection for an in-memory cache, or each connection would see its own empty database.
            self._memory: sqlite3.Connection | None = sqlite3.connect(
                self.path, timeout=30, isolation_level=None, check_same_thread=False
            )
        else:
            self._memory = None
        with self._connect() as db:
            db.executescript(SCHEMA)
        self.last_source = ""

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """A connection per call, so a cache made on one thread works on another (the MCP server's)."""
        if self._memory is not None:
            yield self._memory
            return
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            yield db
        finally:
            db.close()

    def _get(self, owner: str, number: int) -> tuple[float, list[Item]] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT fetched_at, items FROM board_cache WHERE owner=? AND number=?",
                (owner, number),
            ).fetchone()
        if row is None:
            return None
        return float(row[0]), [Item(**d) for d in json.loads(row[1])]

    def put(self, owner: str, number: int, items: Sequence[Item]) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO board_cache (owner, number, fetched_at, items) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (owner, number) DO UPDATE SET fetched_at=excluded.fetched_at, items=excluded.items",
                (owner, number, self.clock(), json.dumps([asdict(i) for i in items])),
            )

    def items(
        self, client: ItemReader, owner: str, number: int, *, fresh: bool = False
    ) -> list[Item]:
        """The Project's items: cached when younger than max_age_s, else one paginated read."""
        cached = self._get(owner, number)
        if cached is not None and not fresh:
            age = self.clock() - cached[0]
            if age < self.max_age_s:
                self.last_source = f"cache ({int(age)}s old)"
                return cached[1]
        if self.budget.reads_low():
            if cached is not None:
                self.last_source = (
                    f"cache ({int(self.clock() - cached[0])}s old; rate budget low)"
                )
                return cached[1]
            raise self.budget.read_low_error()
        items = client.items(owner, number)
        self.put(owner, number, items)
        self.last_source = "live"
        return items

    def patch(self, owner: str, number: int, item: Item) -> None:
        """Replace one item in a cached entry, keeping its age, so a won claim is visible at once."""
        cached = self._get(owner, number)
        if cached is None:
            return
        fetched_at, items = cached
        replaced = [item if i.id == item.id else i for i in items]
        with self._connect() as db:
            db.execute(
                "UPDATE board_cache SET items=? WHERE owner=? AND number=? AND fetched_at=?",
                (json.dumps([asdict(i) for i in replaced]), owner, number, fetched_at),
            )

    def invalidate(self, owner: str, number: int) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM board_cache WHERE owner=? AND number=?", (owner, number)
            )
