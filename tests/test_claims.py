"""Claims decided on Project 1: the race, takeover, heartbeat, release, pickup exclusion, the rate budget, the
rebuild signal and the offline refusal. Every board here is tests/fakeboard.py; nothing reaches GitHub."""

from __future__ import annotations

import json
import pathlib
import threading
from dataclasses import replace

import pytest

import fakeboard as fb
from projecthub.github import boardcache, claims, dispatch, outbox, ratebudget
from projecthub.github import client as g
from projecthub.mcp import loop
from projecthub.mcp import read as planner
from projecthub.mcp import work as L

ROW = "[pkix] anchors.load_anchor(): trust anchor selection"
OTHER = "[pkix] chain.build(): path building"


@pytest.fixture
def clock() -> fb.Clock:
    return fb.Clock()


@pytest.fixture
def store(tmp_path, clock) -> L.Store:
    return L.Store(tmp_path / "leases.sqlite", now=clock)


def drain(store: L.Store, board: fb.FakeBoard, clock: fb.Clock, **kw) -> outbox.Report:
    return outbox.drain(
        store,
        board,
        state=outbox.DrainState(store.path),
        clock=clock,
        sleep=clock.sleep,
        rate=kw.pop("rate", ratebudget.RateBudget(clock=clock)),
        **kw,
    )


# -- the claim ----------------------------------------------------------------------------------------


def test_a_claim_writes_the_four_fields_rereads_and_mirrors_a_lease(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    lease = fb.claimant(store, board, clock).acquire(
        "pkix", "anchors.load_anchor", "agent-7"
    )
    values = board.by_id["PVTI-[pkix]-anchors.load_anchor()"].values
    token = L.claim_token("agent-7", lease.lease_id)
    assert values["Agent"] == token and values["Status"] == "In progress"
    assert values["Claim expires"] == claims.iso(lease.expires_at)
    assert values["Claimed"] == claims.iso(lease.acquired_at)[:10]
    assert len(board.writes) == 1, "one request carries the whole claim"
    assert board.reads["item"] == 2, "read before the write, and again after the settle"
    assert clock.sleeps == [claims.SETTLE_S]
    assert store.held("pkix:anchors.load_anchor").lease_id == lease.lease_id
    assert [x for x in store.pending() if x["sink"] == "board"] == [], (
        "the claim went to the Project synchronously; nothing for it is queued"
    )


def test_two_claimants_racing_for_one_item_get_exactly_one_claim(tmp_path, clock):
    """Both read the item free, both write, then both re-read: the last writer holds it, the other is told."""

    class RaceBoard(fb.FakeBoard):
        def __init__(self, *items: g.Item) -> None:
            super().__init__(*items)
            self.lock = threading.Lock()
            self.a_wrote = threading.Event()
            self.both_read = threading.Barrier(2, timeout=10)
            self.local = threading.local()

        def item(self, item_id: str) -> g.Item:
            first = not getattr(self.local, "read", False)
            self.local.read = True
            if first:
                self.both_read.wait()  # neither writes before both have read it free
            with self.lock:
                return super().item(item_id)

        def set_values(self, project, changes):  # type: ignore[no-untyped-def]
            if threading.current_thread().name == "B":
                assert self.a_wrote.wait(10)
            with self.lock:
                out = super().set_values(project, changes)
            if threading.current_thread().name == "A":
                self.a_wrote.set()
            return out

    board = RaceBoard(fb.item(ROW, Status="Ready"))
    settled = threading.Barrier(2, timeout=10)
    results: dict[str, object] = {}

    def run(name: str) -> None:
        store = L.Store(tmp_path / f"{name}.sqlite", now=clock)
        claimant = fb.claimant(store, board, clock)
        claimant.sleep = lambda _s: settled.wait()  # both re-read only after both wrote
        try:
            results[name] = claimant.acquire(
                "pkix", "anchors.load_anchor", f"agent-{name}"
            )
        except L.LeaseError as error:
            results[name] = error

    threads = [threading.Thread(target=run, args=(n,), name=n) for n in "AB"]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert isinstance(results["A"], L.LeaseError) and results["A"].code == "LEASE_HELD"
    assert "lost the claim" in str(results["A"])
    winner = results["B"]
    assert isinstance(winner, L.Lease)
    agent = board.by_id["PVTI-[pkix]-anchors.load_anchor()"].values["Agent"]
    assert agent == L.claim_token("agent-B", winner.lease_id)


def test_a_live_claim_is_refused_and_an_expired_one_is_taken_over(store, clock):
    held = fb.item(
        ROW,
        Status="In progress",
        Agent="agent-1 [aaaaaaaa]",
        **{"Claim expires": claims.iso(clock.t + 600)},
    )
    board = fb.FakeBoard(held)
    claimant = fb.claimant(store, board, clock)
    with pytest.raises(L.LeaseError) as caught:
        claimant.acquire("pkix", "anchors.load_anchor", "agent-2")
    assert caught.value.code == "LEASE_HELD" and "agent-1 [aaaaaaaa]" in str(
        caught.value
    )
    assert board.writes == []

    clock.t += 601  # agent-1 stopped heartbeating
    lease = claimant.acquire("pkix", "anchors.load_anchor", "agent-2")
    values = board.by_id[held.id].values
    assert values["Agent"] == L.claim_token("agent-2", lease.lease_id)
    assert claims.parse_iso(values["Claim expires"]) == lease.expires_at


def test_claimable_means_an_open_status_and_no_live_claim():
    now = 1_800_000_000.0
    soon = {"Claim expires": claims.iso(now + 60)}
    assert claims.claimable(fb.item(ROW, Status="Ready"), now)
    assert claims.claimable(fb.item(ROW, Status="Backlog", Agent="x [1]"), now), (
        "an Agent with no Claim expires predates the protocol and holds nothing"
    )
    assert not claims.claimable(
        fb.item(ROW, Status="Ready", Agent="x [1]", **soon), now
    )
    assert not claims.claimable(fb.item(ROW, Status="Blocked"), now)
    assert not claims.claimable(fb.item(ROW, Status="In review"), now)
    assert not claims.claimable(fb.item(ROW, Status="Done"), now)
    assert claims.agent_name("agent-7 [3f2a91c0]") == "agent-7"


def test_one_live_claim_per_holder_on_the_project(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"), fb.item(OTHER, Status="Ready"))
    claimant = fb.claimant(store, board, clock)
    claimant.acquire("pkix", "anchors.load_anchor", "agent-7")
    with pytest.raises(L.LeaseError, match="HOLDER_BUSY"):
        claimant.acquire("pkix", "chain.build", "agent-7")


def test_a_project_without_claim_expires_refuses_and_says_how_to_add_it(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"), with_claim_field=False)
    with pytest.raises(L.LeaseError, match="NO_CLAIM_FIELD.*ensure-claim-field"):
        fb.claimant(store, board, clock).acquire("pkix", "anchors.load_anchor")
    assert board.writes == []


def test_ensure_claim_field_creates_a_text_field_once():
    board = fb.FakeBoard(with_claim_field=False)
    project, created = claims.ensure_claim_field(board, "o", 1)
    assert created and board.created == [("P1", "Claim expires", "TEXT")]
    assert project.fields["Claim expires"].data_type == "TEXT"
    assert claims.ensure_claim_field(board, "o", 1)[1] is False
    assert len(board.created) == 1, "an existing field is never created again"

    wrong = fb.FakeBoard(with_claim_field=False)
    wrong.create_field("P1", "Claim expires", "DATE")
    with pytest.raises(g.GhProjectsError, match="needs TEXT"):
        claims.ensure_claim_field(wrong, "o", 1)


# -- offline -----------------------------------------------------------------------------------------


def test_an_offline_claim_is_refused_and_nothing_is_recorded(store, clock):
    with pytest.raises(L.LeaseError) as caught:
        fb.claimant(store, None, clock).acquire("pkix", "anchors.load_anchor")
    assert caught.value.code == "OFFLINE" and "no claim was made" in str(caught.value)

    unreachable = fb.FakeBoard(
        fb.item(ROW, Status="Ready"), offline="could not resolve host"
    )
    with pytest.raises(L.LeaseError, match="OFFLINE.*could not resolve host"):
        fb.claimant(store, unreachable, clock).acquire("pkix", "anchors.load_anchor")
    assert store.live() == [] and store.pending() == []


def test_the_other_tools_still_work_offline(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    lease = fb.claimant(store, board, clock).acquire("pkix", "anchors.load_anchor")
    offline = fb.claimant(store, None, clock)
    clock.t += 60
    assert offline.heartbeat(lease.lease_id) == clock.t + L.DEFAULT_TTL
    store.note(lease.lease_id, "progress", "halfway")
    assert (
        offline.release(lease.lease_id, "handoff", note="next: the decoder")
        == "released"
    )
    assert len(store.pending()) == 1, "queued for the drain, one coalesced write"


# -- heartbeat and release ---------------------------------------------------------------------------


def test_a_heartbeat_extends_claim_expires_through_the_outbox(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    claimant = fb.claimant(store, board, clock)
    lease = claimant.acquire("pkix", "anchors.load_anchor", "agent-7")
    clock.t += 600
    expires = claimant.heartbeat(lease.lease_id)
    assert expires == clock.t + L.DEFAULT_TTL
    queued = store.pending()[0]["payload"]
    assert queued["claim"] == L.claim_token("agent-7", lease.lease_id)
    assert queued["fields"]["Claim expires"] == claims.iso(expires)
    report = drain(store, board, clock)
    assert report.done and not report.conflicts
    assert board.by_id["PVTI-[pkix]-anchors.load_anchor()"].values[
        "Claim expires"
    ] == claims.iso(expires)


def test_a_heartbeat_on_a_claim_lost_on_the_project_ends_it(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    claimant = fb.claimant(store, board, clock)
    lease = claimant.acquire("pkix", "anchors.load_anchor", "agent-7")
    rival = {"Agent": "agent-9 [99999999]", "Claim expires": claims.iso(clock.t + 3600)}
    item_id = "PVTI-[pkix]-anchors.load_anchor()"
    current = board.by_id[item_id]
    board.by_id[item_id] = replace(current, values={**current.values, **rival})
    claimant.cache.invalidate(claimant.owner, claimant.number)
    with pytest.raises(L.LeaseError, match="LEASE_LOST.*agent-9"):
        claimant.heartbeat(lease.lease_id)
    assert store.live() == [] and store.pending() == [], (
        "nothing queued against the winner"
    )


def test_release_clears_the_claim_and_hands_the_row_back(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    claimant = fb.claimant(store, board, clock)
    lease = claimant.acquire("pkix", "anchors.load_anchor", "agent-7")
    clock.t += 30
    claimant.release(lease.lease_id, "handoff", note="the decoder is next")
    report = drain(store, board, clock)
    values = board.by_id["PVTI-[pkix]-anchors.load_anchor()"].values
    assert report.conflicts == [], (
        "the claim's own In progress is not mistaken for a person's move"
    )
    assert values["Status"] == "Ready"
    assert "Agent" not in values and "Claim expires" not in values
    assert "the decoder" not in json.dumps(board.writes, default=str), (
        "note text stays local"
    )
    assert claims.claimable(board.by_id["PVTI-[pkix]-anchors.load_anchor()"], clock.t)


def test_pr_opened_keeps_the_agent_and_block_keeps_it_too(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"), fb.item(OTHER, Status="Ready"))
    claimant = fb.claimant(store, board, clock)
    first = claimant.acquire("pkix", "anchors.load_anchor", "agent-7")
    claimant.release(first.lease_id, "pr_opened", pr_url="https://x/pull/1")
    second = claimant.acquire("pkix", "chain.build", "agent-7")
    claimant.block(second.lease_id, "spec_question", "which clause")
    drain(store, board, clock)
    reviewed = board.by_id["PVTI-[pkix]-anchors.load_anchor()"].values
    blocked = board.by_id["PVTI-[pkix]-chain.build()"].values
    assert reviewed["Status"] == "In review" and reviewed["Agent"].startswith(
        "agent-7 ["
    )
    assert blocked["Status"] == "Blocked" and blocked["Agent"].startswith("agent-7 [")
    assert "Claim expires" not in reviewed and "Claim expires" not in blocked


def test_a_release_from_a_lost_claim_never_undoes_the_winner(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    lease = fb.claimant(store, board, clock).acquire(
        "pkix", "anchors.load_anchor", "agent-7"
    )
    winner = "agent-9 [99999999]"
    item_id = "PVTI-[pkix]-anchors.load_anchor()"
    board.set_values(board.board, [g.Change(item_id, "Agent", winner)])
    store.release(lease.lease_id, "abandoned")
    report = drain(store, board, clock)
    assert board.by_id[item_id].values["Agent"] == winner
    assert board.by_id[item_id].values["Status"] == "In progress"
    assert report.conflicts and "the claim was lost" in report.conflicts[0]


# -- pickup and next read the Project ---------------------------------------------------------------


def test_pickup_passes_over_an_item_someone_else_holds(store, clock, monkeypatch):
    import projecthub.github.sync as sync_module

    monkeypatch.setattr(sync_module, "ISSUE_REPO", "b1tninja-labs/portfolio")
    monkeypatch.setattr(loop, "blockings", lambda root, project: ([], []))
    held = fb.item(
        ROW,
        Status="Ready",
        Agent="agent-1 [aaaaaaaa]",
        **{"Claim expires": claims.iso(clock.t + 900)},
    )
    pkix = g.Item(
        "PVTI-p",
        "ISSUE",
        "I-p",
        "u",
        "pkix: work",
        {"Level": "Project", "Active": "Active"},
    )
    board = fb.FakeBoard(held, fb.item(OTHER, Status="Backlog"), pkix)
    claimant = fb.claimant(store, board, clock)
    cards = claimant.cache.items(board, claimant.owner, claimant.number)
    code, text = loop.run_pickup(
        pathlib.Path("D:/code"),
        cards,
        store=claimant,
        make_packet=lambda key: {"row_id": key},
        who="agent-2",
        file_rows=lambda name: [
            {"row": "anchors.load_anchor", "status": "[ ]"},
            {"row": "chain.build", "status": "[ ]"},
        ],
        matrix_for=lambda name: pathlib.Path(f"{name}/ROADMAP.md"),
        now=clock.t,
    )
    assert code == 0, text
    assert text.startswith("# Your assignment: pkix:chain.build")
    assert board.by_id[held.id].values["Agent"] == "agent-1 [aaaaaaaa]", (
        "the held item was not touched"
    )
    assert board.reads["items"] == 1, (
        "one paginated read per run: the claim used the cache"
    )


def test_pickup_reports_an_offline_claim_rather_than_starting(
    store, clock, monkeypatch
):
    import projecthub.github.sync as sync_module

    monkeypatch.setattr(sync_module, "ISSUE_REPO", "b1tninja-labs/portfolio")
    monkeypatch.setattr(loop, "blockings", lambda root, project: ([], []))
    pkix = g.Item(
        "PVTI-p",
        "ISSUE",
        "I-p",
        "u",
        "pkix: work",
        {"Level": "Project", "Active": "Active"},
    )
    cards = [fb.item(ROW, Status="Ready"), pkix]
    code, text = loop.run_pickup(
        pathlib.Path("D:/code"),
        cards,
        store=fb.claimant(store, None, clock),
        make_packet=lambda key: {"row_id": key},
        file_rows=lambda name: [{"row": "anchors.load_anchor", "status": "[ ]"}],
        matrix_for=lambda name: pathlib.Path(f"{name}/ROADMAP.md"),
        now=clock.t,
    )
    assert code == loop.OFFLINE_EXIT and text.startswith("CANNOT CLAIM: OFFLINE")


def test_a_declared_non_goal_is_never_ready(monkeypatch, tmp_path):
    import projecthub.github.sync as sync_module

    monkeypatch.setattr(sync_module, "ISSUE_REPO", "")
    monkeypatch.setattr(loop, "blockings", lambda root, project: ([], []))
    url = "https://github.com/b1tninja/pkix/issues/1"
    cards = [
        g.Item(
            "P",
            "ISSUE",
            "N",
            url,
            "pkix: work",
            {"Level": "Project", "Active": "Active"},
        ),
        g.Item(
            "I",
            "ISSUE",
            "N1",
            url,
            "never.ever()",
            {"Level": "Obligation", "Status": "Ready"},
        ),
    ]
    step = loop.pick(
        pathlib.Path("D:/code"),
        cards,
        file_rows=lambda name: [{"row": "never.ever", "status": "[no]"}],
        matrix_for=lambda name: pathlib.Path(f"{name}/ROADMAP.md"),
    )
    assert "stop" in step
    assert "non-goal" in step["skipped"][0]["why"]
    assert planner.is_open({"status": "[no]"}) is False
    assert planner.is_open({"status": "[ ]"}) and planner.is_open({"status": "[part]"})
    assert planner.is_open({"status": "[ok]"}) is False


# -- the board cache -------------------------------------------------------------------------------


def test_the_board_is_read_once_a_minute_and_served_from_cache_when_the_budget_is_low(
    tmp_path, clock
):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    rate = ratebudget.RateBudget(clock=clock)
    cache = boardcache.BoardCache(tmp_path / "c.sqlite", clock=clock, budget=rate)
    cache.items(board, "o", 1)
    clock.t += 59
    cache.items(board, "o", 1)
    assert board.reads["items"] == 1 and cache.last_source.startswith("cache")
    clock.t += 2
    cache.items(board, "o", 1)
    assert board.reads["items"] == 2, "older than 60 s: read again"
    rate.observe_graphql(
        {"remaining": 3, "limit": 5000, "cost": 1, "resetAt": claims.iso(clock.t + 900)}
    )
    clock.t += 120
    assert cache.items(board, "o", 1)[0].title == ROW
    assert board.reads["items"] == 2 and "rate budget low" in cache.last_source
    other = boardcache.BoardCache(tmp_path / "other.sqlite", clock=clock, budget=rate)
    with pytest.raises(ratebudget.BudgetLow, match="rate budget low until"):
        other.items(board, "o", 1)


# -- the rate budget --------------------------------------------------------------------------------


def test_a_low_budget_refuses_claims_and_drains_with_its_reset_time(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    rate = ratebudget.RateBudget(clock=clock)
    reset = claims.iso(clock.t + 1200)
    rate.observe_graphql({"remaining": 100, "limit": 5000, "cost": 1, "resetAt": reset})
    with pytest.raises(L.LeaseError) as caught:
        fb.claimant(store, board, clock, budget=rate).acquire(
            "pkix", "anchors.load_anchor"
        )
    assert caught.value.code == "RATE_BUDGET_LOW"
    assert f"rate budget low until {reset}" in str(caught.value)

    lease = store.acquire(
        "pkix", "anchors.load_anchor"
    )  # a local lease, so something is queued
    store.release(lease.lease_id, "handoff")
    report = drain(store, board, clock, rate=rate)
    assert report.stopped.startswith(f"rate budget low until {reset}")
    assert board.writes == [] and store.pending(), "nothing written, nothing lost"
    clock.t += 1201
    assert not rate.reads_low(), "a window past its reset is not trusted as low"


def test_every_query_carries_rate_limit_and_the_client_reports_it():
    seen: list[str] = []

    def runner(argv: list[str], stdin: str | None) -> g.Completed:
        body = json.loads(stdin or "{}")
        seen.append(body.get("query", ""))
        data: dict[str, object] = {"viewer": {"login": "x"}}
        if "rateLimit" in body.get("query", ""):
            data["rateLimit"] = {
                "limit": 5000,
                "cost": 1,
                "remaining": 4321,
                "resetAt": "2030-01-01T00:00:00Z",
            }
        return g.Completed(0, json.dumps({"data": data}), "")

    rate = ratebudget.RateBudget(clock=lambda: 1_800_000_000.0)
    original, ratebudget.BUDGET = ratebudget.BUDGET, rate
    try:
        g.Projects(runner).graphql("query { viewer { login } }")
        assert "rateLimit { limit cost remaining resetAt }" in seen[0]
        assert rate.graphql.remaining == 4321 and rate.graphql.last_cost == 1
        g.Projects(runner).graphql("mutation { x { y } }")
        assert "rateLimit" not in seen[1], "Mutation has no rateLimit field"
        assert rate.graphql.remaining == 4320, (
            "a mutation is counted until GitHub reports again"
        )
    finally:
        ratebudget.BUDGET = original
    rate.observe_headers(
        {
            "X-RateLimit-Remaining": "12",
            "X-RateLimit-Limit": "5000",
            "X-RateLimit-Reset": "1900000000",
            "X-RateLimit-Resource": "core",
        }
    )
    assert rate.rest.remaining == 12
    with pytest.raises(ratebudget.BudgetLow, match="REST"):
        rate.require_write()
    assert rate.snapshot()["writes"] == "refused"


# -- the rebuild signal ------------------------------------------------------------------------------


def test_the_rebuild_signal_is_debounced_to_one_every_ten_minutes(tmp_path, clock):
    import sqlite3

    db = sqlite3.connect(tmp_path / "o.sqlite", isolation_level=None)
    board = fb.FakeBoard()
    sent = dispatch.signal_rebuild(
        board, db, changed=3, repo="o/portfolio", clock=clock
    )
    assert sent.sent and board.dispatched[0][:2] == (
        "o/portfolio",
        "projecthub-state-changed",
    )
    assert board.dispatched[0][2]["writes"] == 3
    clock.t += 300
    held = dispatch.signal_rebuild(
        board, db, changed=1, repo="o/portfolio", clock=clock
    )
    assert not held.sent and "owed" in held.why and len(board.dispatched) == 1
    clock.t += 301
    owed = dispatch.signal_rebuild(
        board, db, changed=0, repo="o/portfolio", clock=clock
    )
    assert owed.sent, "a change inside the window is sent once the window passes"
    clock.t += 601
    quiet = dispatch.signal_rebuild(
        board, db, changed=0, repo="o/portfolio", clock=clock
    )
    assert not quiet.sent and len(board.dispatched) == 2


def test_a_drain_that_wrote_sends_one_dispatch_and_one_that_did_not_sends_none(
    store, clock
):
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    lease = fb.claimant(store, board, clock).acquire("pkix", "anchors.load_anchor")
    assert drain(store, board, clock).writes == 0
    assert board.dispatched == [], "nothing changed, nothing owed"
    store.release(lease.lease_id, "handoff")
    report = drain(store, board, clock, repo="o/portfolio")
    assert report.writes > 0 and len(board.dispatched) == 1
    assert report.dispatch.startswith("sent")


def test_a_failed_dispatch_is_reported_and_stays_owed(tmp_path, clock):
    import sqlite3

    class Refusing:
        def dispatch(
            self, repo: str, event_type: str, client_payload: dict[str, object]
        ) -> None:
            raise g.GhProjectsError("HTTP 404")

    db = sqlite3.connect(tmp_path / "o.sqlite", isolation_level=None)
    got = dispatch.signal_rebuild(Refusing(), db, changed=1, clock=clock)
    assert not got.sent and "HTTP 404" in got.why
    retried = dispatch.signal_rebuild(fb.FakeBoard(), db, changed=0, clock=clock)
    assert retried.sent, "a failed dispatch never started the debounce window"
