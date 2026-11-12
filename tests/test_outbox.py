"""The outbox drain, against a fake store and a fake client. Nothing here touches the network or a token."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from projecthub.github import client as g
from projecthub.github import outbox

STATUS = ("Triage", "Backlog", "Ready", "In progress", "In review", "Blocked", "Done")


def board_project() -> g.Project:
    return g.Project(
        id="P1",
        number=1,
        title="portfolio",
        closed=False,
        fields={
            "Status": g.Field(
                "F-S", "Status", "SINGLE_SELECT", {s: f"O-{s}" for s in STATUS}
            ),
            "Claimed": g.Field("F-C", "Claimed", "DATE"),
            "Agent": g.Field("F-A", "Agent", "TEXT"),
            "Last activity": g.Field("F-L", "Last activity", "TEXT"),
        },
    )


class FakeStore:
    def __init__(self, *entries: dict[str, Any]) -> None:
        self.entries = [
            {"id": i, "sink": "board", "key": "", "attempts": 0, **e}
            for i, e in enumerate(entries, start=1)
        ]
        self.done: list[int] = []
        self.retried: list[tuple[int, float]] = []
        self.delivered: list[dict[str, Any] | None] = []

    def pending(self, limit: int) -> list[dict[str, Any]]:
        gone = set(self.done) | {i for i, _ in self.retried}
        return [e for e in self.entries if e["id"] not in gone][:limit]

    def mark_done(self, id: int, payload: dict[str, Any] | None = None) -> bool:
        self.done.append(id)
        self.delivered.append(payload)
        return True

    def retry_later(self, id: int, delay_s: float) -> None:
        self.retried.append((id, delay_s))


class FakeClient:
    def __init__(self, *items: g.Item, fail: str = "") -> None:
        self._items = list(items)
        self.writes: list[list[g.Change]] = []
        self.issues: list[dict[str, Any]] = []
        self.added: list[str] = []
        self.fail = fail

    def project(self, owner: str, number: int) -> g.Project:
        return board_project()

    def items(self, owner: str, number: int) -> list[g.Item]:
        return list(self._items)

    def set_values(
        self, project: g.Project, changes: Sequence[g.Change]
    ) -> list[g.Outcome]:
        if self.fail:
            raise g.GhProjectsError(self.fail)
        self.writes.append(list(changes))
        return [g.Outcome(c, True) for c in changes]

    def add_items(self, project_id: str, content_ids: Sequence[str]) -> dict[str, str]:
        self.added += content_ids
        return {c: f"PVTI-{c}" for c in content_ids}

    def graphql(
        self, query: str, variables: dict[str, object] | None = None
    ) -> dict[str, Any]:
        variables = variables or {}
        if "repository(" in query:
            return {"repository": {"id": f"R-{variables['o']}/{variables['n']}"}}
        if "createIssue" in query:
            self.issues.append(dict(variables["i"]))  # type: ignore[call-overload]
            return {
                "createIssue": {"issue": {"id": f"I{len(self.issues)}", "url": "u"}}
            }
        raise AssertionError(query)


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def card(title: str, **values: object) -> g.Item:
    return g.Item(
        f"PVTI-{title[:12]}", "ISSUE", f"I-{title[:12]}", "", title, dict(values)
    )


PKIX = "[pkix] anchors.load_anchor(): trust anchor selection"


def claim(status: str = "In progress", **extra: object) -> dict[str, Any]:
    return {
        "key": "pkix:anchors.load_anchor",
        "payload": {
            "row_key": "pkix:anchors.load_anchor",
            "fields": {
                "Status": status,
                "Claimed": "2026-11-11",
                "Agent": "agent-7",
                "Last activity": "claim 2026-11-11T10:00:00+00:00",
                **extra,
            },
        },
    }


def run(store: FakeStore, client: FakeClient, **kw: Any) -> outbox.Report:
    clock = kw.pop("clock", Clock())
    state = kw.pop("state", outbox.DrainState())
    return outbox.drain(
        store, client, state=state, clock=clock, sleep=clock.sleep, **kw
    )


def written(client: FakeClient) -> dict[str, object]:
    return {c.field_name: c.value for batch in client.writes for c in batch}


def test_fields_map_onto_the_matching_item_and_the_entry_is_done():
    store = FakeStore(claim(Note="private words"))
    client = FakeClient(
        card("[certledger] anchors.load_anchor(): other"), card(PKIX, Status="Ready")
    )
    report = run(store, client)
    assert written(client) == {
        "Status": "In progress",
        "Claimed": "2026-11-11",
        "Agent": "agent-7",
        "Last activity": "claim 2026-11-11T10:00:00+00:00",
    }
    assert {c.item_id for c in client.writes[0]} == {"PVTI-[pkix] ancho"}
    assert store.done == [1]
    assert any("Note" in s for s in report.skipped)


def test_several_entries_go_in_one_batched_request():
    a = "[pkix] a.one(): x"
    b = "[pkix] b.two(): y"
    store = FakeStore(
        {"payload": {"row_key": "pkix:a.one", "fields": {"Agent": "x"}}},
        {"payload": {"row_key": "pkix:b.two", "fields": {"Agent": "y"}}},
    )
    client = FakeClient(card(a), card(b))
    run(store, client)
    assert len(client.writes) == 1 and len(client.writes[0]) == 2
    assert store.done == [1, 2]


def test_a_missing_item_is_retried_with_backoff_not_dropped():
    store = FakeStore(claim())
    store.entries[0]["attempts"] = 2
    client = FakeClient(card("[pkix] something.else(): no"))
    report = run(store, client)
    assert client.writes == []
    assert store.done == []
    assert store.retried == [(1, outbox.backoff(2))]
    assert outbox.backoff(2) > outbox.backoff(1)
    assert "no item" in report.retried[0][1]


def test_a_transient_error_is_retried():
    store = FakeStore(claim())
    client = FakeClient(card(PKIX), fail="HTTP 502")
    report = run(store, client)
    assert store.done == [] and [i for i, _ in store.retried] == [1]
    assert report.retried[0][1].startswith("transient")


def test_an_owner_moved_status_is_left_and_recorded():
    state = outbox.DrainState()
    item = card(PKIX, Status="Blocked")
    state.record(item.id, "Status", "In progress", 0.0)  # projecthub's last write
    store = FakeStore(claim("In review"))
    client = FakeClient(item)
    report = run(store, client, state=state)
    assert "Status" not in written(client)
    assert written(client)["Agent"] == "agent-7"
    assert store.done == [1]
    (conflict,) = state.conflicts()
    assert conflict["live"] == "Blocked" and conflict["last"] == "In progress"
    assert conflict["wanted"] == "In review"
    assert report.conflicts


def test_status_matching_projecthubs_last_write_is_updated():
    state = outbox.DrainState()
    item = card(PKIX, Status="In progress")
    state.record(item.id, "Status", "In progress", 0.0)
    client = FakeClient(item)
    run(FakeStore(claim("In review")), client, state=state)
    assert written(client)["Status"] == "In review"
    assert state.last(item.id, "Status") == "In review"
    assert state.conflicts() == []


def test_done_is_never_written_and_a_done_card_is_never_moved():
    client = FakeClient(card(PKIX, Status="Ready"))
    report = run(FakeStore(claim("Done")), client)
    assert "Status" not in written(client)
    assert any("Done" in s for s in report.skipped)

    client = FakeClient(card(PKIX, Status="Done"))
    report = run(FakeStore(claim("In progress")), client)
    assert "Status" not in written(client)
    assert report.conflicts


def test_writes_are_paced_and_held_under_the_minute_ceiling():
    rows = [f"[pkix] r{i}.x(): s" for i in range(6)]
    store = FakeStore(
        *(
            {"payload": {"row_key": f"pkix:r{i}.x", "fields": {"Agent": "a"}}}
            for i in range(6)
        )
    )
    client = FakeClient(*(card(t) for t in rows))
    clock = Clock()
    state = outbox.DrainState()
    budget = outbox.Budget(
        state, pace_s=2.0, per_minute=3, clock=clock, sleep=clock.sleep
    )
    for _ in range(6):
        budget.take(1)
    stamps = state.stamps(0)
    assert len(stamps) == 6
    assert all(b - a >= 2.0 for a, b in zip(stamps, stamps[1:], strict=False))
    assert all(
        len([t for t in stamps if s - 60 < t <= s]) <= 3 for s in stamps
    )  # never more than 3 in any minute
    # And the drain spends through the same budget.
    report = run(store, client, pace_s=2.0, clock=clock)
    assert report.writes == 6 and store.done == [1, 2, 3, 4, 5, 6]


def test_a_spent_hour_stops_the_run_and_leaves_entries_pending():
    clock = Clock()
    state = outbox.DrainState()
    state.wrote(clock() - 10, 450)
    store = FakeStore(claim())
    client = FakeClient(card(PKIX))
    report = run(store, client, clock=clock, state=state)
    assert "hour" in report.stopped
    assert client.writes == [] and store.done == [] and store.retried == []


def test_limits_above_githubs_are_refused():
    with pytest.raises(ValueError):
        run(FakeStore(), FakeClient(), per_minute=81)


def test_a_proposal_becomes_an_issue_and_a_triage_item_not_a_draft():
    state = outbox.DrainState()
    store = FakeStore(
        {
            "key": "p-12",
            "payload": {
                "proposal_id": "pkix:12",
                "fields": {"Status": "Triage"},
                "title": "split the anchor loader",
                "kind": "row",
            },
        }
    )
    client = FakeClient()
    run(store, client, state=state, repo="b1tninja-labs/portfolio")
    (issue,) = client.issues
    assert issue["title"] == "[pkix] proposal-12: split the anchor loader"
    assert issue["repositoryId"] == "R-b1tninja-labs/portfolio"
    assert client.added == ["I1"]
    assert written(client) == {"Status": "Triage"}
    assert store.done == [1]

    # Run again (say mark_done was lost): no second issue.
    store.done.clear()
    client2 = FakeClient(
        card("[pkix] proposal-12: split the anchor loader", Status="Triage")
    )
    run(store, client2, state=state)
    assert client2.issues == [] and client2.writes == []


def test_a_private_proposal_is_never_published():
    store = FakeStore(
        {"payload": {"proposal_id": "pkix:9", "title": "t", "private": True}}
    )
    client = FakeClient()
    run(store, client)
    assert client.issues == [] and client.added == [] and store.done == [1]


def test_dry_run_writes_nothing_and_says_what_it_would_write():
    store = FakeStore(claim())
    client = FakeClient(card(PKIX))
    report = run(store, client, dry_run=True)
    assert client.writes == [] and store.done == [] and store.retried == []
    assert any("would set" in line and "Agent" in line for line in report.lines())


def test_a_newer_update_merged_mid_run_is_not_lost(tmp_path):
    """The drain hands back the payload it sent, so the real store keeps an entry pending when a later
    event was merged into it after pending() read it."""
    from projecthub.mcp.work import Store

    store = Store(tmp_path / "l.db")
    lease = store.acquire("alpha", "row", holder="a")
    [entry] = store.pending(10)
    store.note(lease.lease_id, "progress", "local text")
    assert store.mark_done(int(entry["id"]), entry["payload"]) is False
    [still] = store.pending(10)
    assert still["id"] == entry["id"]
    assert "note:progress" in str(still["payload"]["fields"]["Last activity"])
