"""A lease write drains the outbox right away, so the board follows the work without a manual `projecthub drain`.
Every board here is tests/fakeboard.py; nothing reaches GitHub."""

from __future__ import annotations

import pytest

import fakeboard as fb
from projecthub.github import outbox
from projecthub.mcp import work as L

ROW = "[pkix] anchors.load_anchor(): trust anchor selection"
ITEM = "PVTI-[pkix]-anchors.load_anchor()"


@pytest.fixture
def clock() -> fb.Clock:
    return fb.Clock()


@pytest.fixture
def store(tmp_path, clock) -> L.Store:
    return L.Store(tmp_path / "leases.sqlite", now=clock)


def released(store, clock) -> fb.FakeBoard:
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    lease = fb.claimant(store, board, clock).acquire("pkix", "anchors.load_anchor", "h")
    store.release(lease.lease_id, "handoff", note="done for now")
    assert store.pending(10), "a release queues its board write"
    return board


def test_a_release_reaches_the_board_without_a_manual_drain(store, clock, monkeypatch):
    board = released(store, clock)
    monkeypatch.setattr(L, "board_client", lambda: (board, ""))
    monkeypatch.setattr(outbox, "PACE_S", 0.0)
    summary = L.drain_now(store)
    assert summary == "outbox: drained"
    assert store.pending(10) == []
    values = board.by_id[ITEM].values
    assert values.get("Agent") is None and values.get("Claim expires") is None


def test_offline_leaves_the_outbox_queued(store, clock):
    released(store, clock)
    assert L.drain_now(store) == ""  # conftest sets PROJECTHUB_OFFLINE
    assert store.pending(10)


def test_the_opt_out_leaves_the_outbox_queued(store, clock, monkeypatch):
    board = released(store, clock)
    monkeypatch.setattr(L, "board_client", lambda: (board, ""))
    monkeypatch.setenv("PROJECTHUB_NO_AUTODRAIN", "1")
    assert L.drain_now(store) == ""
    assert store.pending(10)


def test_a_failed_drain_never_fails_the_write(store, clock, monkeypatch):
    board = released(store, clock)
    monkeypatch.setattr(L, "board_client", lambda: (board, ""))

    def boom(*args, **kwargs):
        raise RuntimeError("network gone")

    monkeypatch.setattr(outbox, "drain", boom)
    summary = L.drain_now(store)
    assert "not drained" in summary and "projecthub drain" in summary
    assert store.pending(10), "the entry stays queued for the next attempt"


def test_the_release_command_drains(store, clock, tmp_path, monkeypatch, capsys):
    path = tmp_path / "cli.sqlite"
    monkeypatch.setenv("PROJECTHUB_LEASES_DB", str(path))
    cli = L.Store(path, now=clock)
    board = fb.FakeBoard(fb.item(ROW, Status="Ready"))
    lease = fb.claimant(cli, board, clock).acquire("pkix", "anchors.load_anchor", "h")
    monkeypatch.setattr(L, "board_client", lambda: (board, ""))
    assert L.main(["release", lease.lease_id, "handoff", "--note", "n"]) == 0
    assert "outbox: drained" in capsys.readouterr().err
    assert board.by_id[ITEM].values.get("Agent") is None
