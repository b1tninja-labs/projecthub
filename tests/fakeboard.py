"""A Project 1 in memory, for the claim protocol, the board cache, the drain and the rebuild signal.

It holds items by id and applies field writes the way GitHub does: last writer wins, and a None clears.
Nothing here reaches the network; tests/conftest.py also sets PROJECTHUB_OFFLINE so no default client can.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from projecthub.github import client as g
from projecthub.github.boardcache import BoardCache
from projecthub.github.claims import ProjectClaims
from projecthub.github.ratebudget import RateBudget

STATUS = ("Triage", "Backlog", "Ready", "In progress", "In review", "Blocked", "Done")


def project(with_claim_field: bool = True) -> g.Project:
    fields = {
        "Status": g.Field(
            "F-S", "Status", "SINGLE_SELECT", {s: f"O-{s}" for s in STATUS}
        ),
        "Claimed": g.Field("F-C", "Claimed", "DATE"),
        "Agent": g.Field("F-A", "Agent", "TEXT"),
        "Last activity": g.Field("F-L", "Last activity", "TEXT"),
        "Level": g.Field(
            "F-V", "Level", "SINGLE_SELECT", {"Obligation": "O-O", "Project": "O-P"}
        ),
    }
    if with_claim_field:
        fields["Claim expires"] = g.Field("F-E", "Claim expires", "TEXT")
    return g.Project(id="P1", number=1, title="portfolio", closed=False, fields=fields)


def item(title: str, **values: object) -> g.Item:
    slug = title.split(":")[0].replace(" ", "-")
    return g.Item(
        f"PVTI-{slug}",
        "ISSUE",
        f"I-{slug}",
        f"https://github.com/b1tninja-labs/portfolio/issues/{len(title)}",
        title,
        {"Level": "Obligation", **values},
    )


class Clock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


class FakeBoard:
    def __init__(
        self, *items: g.Item, with_claim_field: bool = True, offline: str = ""
    ) -> None:
        self.by_id = {i.id: i for i in items}
        self.board = project(with_claim_field)
        self.offline = offline
        self.writes: list[list[g.Change]] = []
        self.reads = {"items": 0, "item": 0, "project": 0}
        self.created: list[tuple[str, str, str]] = []
        self.dispatched: list[tuple[str, str, dict[str, object]]] = []
        # Hooks a test sets to interleave two claimants: called before a write lands, or a read is served.
        self.before_write: Callable[[Sequence[g.Change]], None] | None = None
        self.before_read: Callable[[str], None] | None = None

    def _net(self) -> None:
        if self.offline:
            raise g.GhProjectsError(f"no usable response (exit 1): {self.offline}")

    def project(self, owner: str, number: int) -> g.Project:
        self._net()
        self.reads["project"] += 1
        return self.board

    def items(self, owner: str, number: int) -> list[g.Item]:
        self._net()
        self.reads["items"] += 1
        return list(self.by_id.values())

    def item(self, item_id: str) -> g.Item:
        self._net()
        if self.before_read is not None:
            self.before_read(item_id)
        self.reads["item"] += 1
        if item_id not in self.by_id:
            raise g.NotFound(item_id)
        return self.by_id[item_id]

    def set_values(
        self, project: g.Project, changes: Sequence[g.Change]
    ) -> list[g.Outcome]:
        self._net()
        if self.before_write is not None:
            hook, self.before_write = self.before_write, None
            hook(changes)
        self.writes.append(list(changes))
        for change in changes:
            current = self.by_id[change.item_id]
            values = dict(current.values)
            if change.value is None:
                values.pop(change.field_name, None)
            else:
                values[change.field_name] = change.value
            self.by_id[change.item_id] = replace(current, values=values)
        return [g.Outcome(c, True) for c in changes]

    def add_items(self, project_id: str, content_ids: Sequence[str]) -> dict[str, str]:
        return {c: f"PVTI-{c}" for c in content_ids}

    def graphql(
        self, query: str, variables: dict[str, object] | None = None
    ) -> dict[str, Any]:
        raise AssertionError(query)

    def create_field(
        self,
        project_id: str,
        name: str,
        data_type: str,
        options: Sequence[dict[str, str]] = (),
        iteration: dict[str, object] | None = None,
    ) -> str:
        self.created.append((project_id, name, data_type))
        fields = dict(self.board.fields)
        fields[name] = g.Field(f"F-{name}", name, data_type)
        self.board = replace(self.board, fields=fields)
        return f"F-{name}"

    def dispatch(
        self, repo: str, event_type: str, client_payload: dict[str, object]
    ) -> None:
        self.dispatched.append((repo, event_type, client_payload))


def claimant(
    store: Any,
    board: FakeBoard | None,
    clock: Clock,
    *,
    budget: RateBudget | None = None,
    cache_path: pathlib.Path | str | None = None,
) -> ProjectClaims:
    rate = budget or RateBudget(clock=clock)
    cache = BoardCache(cache_path or store.path, clock=clock, budget=rate)
    return ProjectClaims(
        store,
        board,
        cache=cache,
        clock=clock,
        sleep=clock.sleep,
        budget=rate,
        offline_reason="" if board else "no client in this test",
    )
