"""Leases: exclusive, self-expiring, owned by their handle, and written nowhere but the local store."""

from __future__ import annotations

import threading

import pytest

from projecthub.mcp import work as L


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def store(tmp_path):
    clock = Clock()
    return L.Store(tmp_path / "leases.sqlite", now=clock), clock


def test_two_concurrent_acquires_of_one_row_yield_exactly_one_lease(tmp_path):
    path = tmp_path / "leases.sqlite"
    L.Store(path)  # create the schema once, before the race
    won: list[str] = []
    refused: list[str] = []
    start = threading.Barrier(8)

    def race(n: int) -> None:
        mine = L.Store(path)
        start.wait()
        try:
            won.append(
                mine.acquire("pkix", "anchors.load_anchor", f"agent-{n}").lease_id
            )
        except L.LeaseError as error:
            refused.append(error.code)

    threads = [threading.Thread(target=race, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(won) == 1, won
    assert refused == ["LEASE_HELD"] * 7


def test_an_expired_lease_cannot_heartbeat_and_frees_the_row(store):
    s, clock = store
    lease = s.acquire("pkix", "a.b", ttl=60)
    clock.t += 30
    assert s.heartbeat(lease.lease_id) == clock.t + 60, (
        "a heartbeat extends by the lease's own TTL"
    )
    clock.t += 61
    with pytest.raises(L.LeaseError) as caught:
        s.heartbeat(lease.lease_id)
    assert caught.value.code == "LEASE_EXPIRED"
    assert s.acquire("pkix", "a.b").lease_id != lease.lease_id, "the row is free again"
    assert [e["kind"] for e in s.events("pkix:a.b")][:3] == [
        "acquire",
        "expire",
        "acquire",
    ]


def test_ttl_is_clamped_and_release_needs_a_known_outcome(store):
    s, clock = store
    lease = s.acquire("pkix", "a.b", ttl=10**9)
    assert lease.expires_at - clock.t == L.MAX_TTL
    with pytest.raises(L.LeaseError, match="BAD_OUTCOME"):
        s.release(lease.lease_id, "done")
    with pytest.raises(L.LeaseError, match="PR_URL_REQUIRED"):
        s.release(lease.lease_id, "pr_opened")
    assert (
        s.release(
            lease.lease_id,
            "pr_opened",
            pr_url="https://github.com/b1tninja/pkix/pull/1",
        )
        == "released"
    )
    with pytest.raises(L.LeaseError, match="LEASE_ENDED"):
        s.heartbeat(lease.lease_id)
    assert s.live_keys() == set()


def test_a_block_that_recurs_escalates_to_a_person(store):
    s, _ = store
    for n in range(3):
        lease = s.acquire("pkix", "a.b")
        got = s.block(lease.lease_id, "spec_question", f"which clause, attempt {n}")
        assert got["times_blocked_this_way"] == n + 1
        assert got["needs_a_person"] is (n + 1 > L.ESCALATE_AFTER)
    decision = s.block(
        s.acquire("pkix", "c.d").lease_id, "decision_needed", "tier choice"
    )
    assert decision["needs_a_person"] is True, "a decision is never an agent's to make"
    with pytest.raises(L.LeaseError, match="DETAIL_REQUIRED"):
        s.block(s.acquire("pkix", "e.f").lease_id, "env", "  ")


def test_an_unknown_handle_is_refused_and_steal_is_a_person_only_path(store):
    s, _ = store
    with pytest.raises(L.LeaseError, match="LEASE_UNKNOWN"):
        s.heartbeat("not-a-lease")
    lease = s.acquire("pkix", "a.b", "agent-1")
    assert s.steal(lease.lease_id, "agent-1 crashed") == "released"
    assert s.acquire("pkix", "a.b", "agent-2").holder == "agent-2"


def _server(store, ready):
    pytest.importorskip("mcp")
    import anyio

    app = L.build(store, ready)
    listed = anyio.run(app.list_tools)
    return app, {t.name: t for t in getattr(listed, "tools", listed)}


def test_the_server_refuses_a_row_that_is_not_ready_and_never_claims_read_only(store):
    import anyio
    from mcp.server.mcpserver.exceptions import ToolError

    s, _ = store
    app, tools = _server(
        s, lambda project, row: "" if row == "a.b" else "project not Active (3)"
    )
    assert sorted(tools) == [
        "forge_block",
        "forge_lease_acquire",
        "forge_lease_heartbeat",
        "forge_lease_release",
        "forge_leases",
    ]
    for name, tool in tools.items():
        assert tool.output_schema, name
        assert tool.annotations.destructive_hint is False, name
        assert tool.annotations.read_only_hint is (name == "forge_leases"), name
    with pytest.raises(ToolError, match="NOT_READY: project not Active"):
        anyio.run(
            app.call_tool, "forge_lease_acquire", {"project": "pkix", "row_id": "x.y"}
        )
    got = anyio.run(
        app.call_tool, "forge_lease_acquire", {"project": "pkix", "row_id": "a.b"}
    ).structured_content
    assert got["row_key"] == "pkix:a.b" and got["lease_id"]
    with pytest.raises(ToolError, match="LEASE_HELD"):
        anyio.run(
            app.call_tool, "forge_lease_acquire", {"project": "pkix", "row_id": "a.b"}
        )
    listed = anyio.run(app.call_tool, "forge_leases", {}).structured_content
    assert [x["row_key"] for x in listed["live"]] == ["pkix:a.b"]
