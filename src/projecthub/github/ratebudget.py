"""The GitHub API budget this process has seen, so a read can fall back to the cache and a write can stop in time.

Every GraphQL query the client sends carries `rateLimit { limit cost remaining resetAt }` (the client adds it),
and every REST reply whose headers the client sees reports `x-ratelimit-*`. Both land here, in one budget per
process. Nothing here sends a request of its own except `projecthub budget --live`.

Two floors, because a read and a write fail differently. A read below `READ_FLOOR` is served from the board
cache, stale or not, since an old view of Project 1 is better than none. A write (a claim or a drain batch)
below `WRITE_FLOOR` is refused with "rate budget low until <time>": a claim half-written to the Project, or a
drain stopped between two items, is worse than one not started. The write floor is the higher one, so a
process that has stopped writing can still read.

The numbers are in docs/rate-budget.md, with the GitHub pages they were checked against.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from projecthub.github.client import RateLimited

# GitHub's primary GraphQL limit for a user token is 5,000 points an hour. A full read of Project 1 is about
# eleven points, a claim three, so these floors leave room for every agent on the machine to finish a step.
READ_FLOOR = int(os.environ.get("PROJECTHUB_READ_FLOOR") or 50)
WRITE_FLOOR = int(os.environ.get("PROJECTHUB_WRITE_FLOOR") or 250)

RATE_FIELDS = "rateLimit { limit cost remaining resetAt }"


class BudgetLow(RateLimited):
    """A write refused, or a read with no cache to fall back on, because the budget is under its floor."""

    def __init__(
        self, resource: str, remaining: int, reset_at: str, floor: int
    ) -> None:
        self.remaining = remaining
        self.reset_at = reset_at
        self.resource = resource
        self.floor = floor
        Exception.__init__(
            self,
            f"rate budget low until {reset_at}: {remaining} {resource} points left, "
            f"and this needs {floor} in hand",
        )


@dataclass
class Window:
    """One resource's latest report: what is left, out of what, and when it refills."""

    limit: int = 0
    remaining: int | None = None
    reset_at: float = 0.0
    last_cost: int = 0
    seen_at: float = 0.0

    def reset_iso(self) -> str:
        if not self.reset_at:
            return "unknown"
        return datetime.fromtimestamp(self.reset_at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RateBudget:
    """The budget for one process: GraphQL points and REST requests, as GitHub last reported them."""

    clock: Callable[[], float] = time.time
    read_floor: int = READ_FLOOR
    write_floor: int = WRITE_FLOOR
    graphql: Window = field(default_factory=Window)
    rest: Window = field(default_factory=Window)
    spent: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- what the client reports -------------------------------------------------------------------

    def observe_graphql(self, rate: Mapping[str, object] | None) -> None:
        """Record a `rateLimit` block from a GraphQL response. A missing or malformed block is ignored."""
        if not isinstance(rate, Mapping) or "remaining" not in rate:
            return
        with self._lock:
            window = self.graphql
            window.remaining = _int(rate.get("remaining"))
            window.limit = _int(rate.get("limit")) or window.limit
            window.last_cost = _int(rate.get("cost"))
            window.reset_at = _epoch(rate.get("resetAt")) or window.reset_at
            window.seen_at = self.clock()
            self.spent += window.last_cost

    def observe_headers(self, headers: Mapping[str, str]) -> None:
        """Record `x-ratelimit-*` response headers; `x-ratelimit-resource` says which window they describe."""
        lowered = {k.strip().lower(): v.strip() for k, v in headers.items()}
        if "x-ratelimit-remaining" not in lowered:
            return
        resource = lowered.get("x-ratelimit-resource", "core")
        with self._lock:
            window = self.graphql if resource == "graphql" else self.rest
            window.remaining = _int(lowered.get("x-ratelimit-remaining"))
            window.limit = _int(lowered.get("x-ratelimit-limit")) or window.limit
            window.reset_at = (
                float(_int(lowered.get("x-ratelimit-reset"))) or window.reset_at
            )
            window.seen_at = self.clock()

    def charge(self, points: int) -> None:
        """A request whose reply carries no rateLimit (a mutation): count it until GitHub reports again."""
        with self._lock:
            if self.graphql.remaining is not None:
                self.graphql.remaining = max(0, self.graphql.remaining - points)
            self.spent += points

    # -- what callers ask --------------------------------------------------------------------------

    def _refilled(self, window: Window) -> bool:
        return bool(window.reset_at) and self.clock() >= window.reset_at

    def known(self) -> bool:
        return self.graphql.remaining is not None and not self._refilled(self.graphql)

    def reads_low(self) -> bool:
        """True when a read should come from the cache. Unknown is not low: the first read finds out."""
        return self.known() and (self.graphql.remaining or 0) < self.read_floor

    def require_write(self, what: str = "write") -> None:
        """Raise BudgetLow when a write must not start. Unknown is allowed; the request reports the budget."""
        if self.known() and (self.graphql.remaining or 0) < self.write_floor:
            raise BudgetLow(
                "GraphQL",
                self.graphql.remaining or 0,
                self.graphql.reset_iso(),
                self.write_floor,
            )
        rest = self.rest
        if (
            rest.remaining is not None
            and not self._refilled(rest)
            and rest.remaining < max(1, self.write_floor // 10)
        ):
            raise BudgetLow(
                "REST", rest.remaining, rest.reset_iso(), self.write_floor // 10
            )

    def read_low_error(self) -> BudgetLow:
        return BudgetLow(
            "GraphQL",
            self.graphql.remaining or 0,
            self.graphql.reset_iso(),
            self.read_floor,
        )

    def snapshot(self) -> dict[str, object]:
        def one(window: Window) -> dict[str, object]:
            return {
                "remaining": window.remaining,
                "limit": window.limit or None,
                "reset_at": window.reset_iso(),
                "last_cost": window.last_cost,
                "stale": window.remaining is None or self._refilled(window),
            }

        return {
            "graphql": one(self.graphql),
            "rest": one(self.rest),
            "spent_this_process": self.spent,
            "read_floor": self.read_floor,
            "write_floor": self.write_floor,
            "reads": "cache only" if self.reads_low() else "live",
            "writes": "refused" if self._writes_refused() else "allowed",
        }

    def _writes_refused(self) -> bool:
        try:
            self.require_write()
        except BudgetLow:
            return True
        return False


def _int(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _epoch(value: object) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# The one budget every client in this process reports to.
BUDGET = RateBudget()


def with_rate_limit(query: str) -> str:
    """`query` with the rateLimit block added before its final brace. Mutations are returned unchanged:
    `rateLimit` is a field of Query, and GitHub's Mutation type has none."""
    text = query.strip()
    if "rateLimit" in text or not (text.startswith("query") or text.startswith("{")):
        return query
    end = text.rfind("}")
    if end < 0:
        return query
    return f"{text[:end]} {RATE_FIELDS} {text[end:]}"


def main(argv: list[str] | None = None) -> int:
    """`projecthub budget`: the budget as GitHub reports it now. Reading `rateLimit` alone was free when
    measured (cost 1 reported, `remaining` unchanged), and GET /rate_limit does not count against the
    primary REST limit."""
    import argparse
    import json
    import sys

    from projecthub.github.client import GhProjectsError, Projects

    parser = argparse.ArgumentParser(
        prog="projecthub budget", description="print the GitHub API budget"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    client = Projects()
    try:
        client.rate_limit()
        client.rest_rate_limit()
    except GhProjectsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    snap = BUDGET.snapshot()
    if args.json:
        print(json.dumps(snap, indent=2))
        return 0
    for name in ("graphql", "rest"):
        window = snap[name]
        assert isinstance(window, dict)
        print(
            f"{name:<8} {window['remaining']} of {window['limit']} left, resets {window['reset_at']}"
        )
    print(f"reads    {snap['reads']} (floor {snap['read_floor']} points)")
    print(f"writes   {snap['writes']} (floor {snap['write_floor']} points)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
