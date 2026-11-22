"""A lease on a row moves its project to Active instead of refusing NOT_READY, and only when inactivity was the
sole obstacle. Every board here is tests/fakeboard.py; nothing reaches GitHub."""

from __future__ import annotations

import pytest

import fakeboard as fb
from projecthub.mcp import read as planner
from projecthub.mcp import work as L

ROW = "[trustatlas] ta.suite_green: AGENTS.md conventions"


@pytest.fixture
def clock() -> fb.Clock:
    return fb.Clock()


@pytest.fixture
def store(tmp_path, clock) -> L.Store:
    return L.Store(tmp_path / "leases.sqlite", now=clock)


def card(name: str, **values: object):
    return fb.item(f"{name}: the project", **{"Level": "Project", **values})


def test_activate_writes_active_on_the_project_card_once(store, clock):
    board = fb.FakeBoard(card("trustatlas"), fb.item(ROW, Status="Backlog"))
    claimant = fb.claimant(store, board, clock)
    assert claimant.activate("trustatlas") is True
    assert board.by_id["PVTI-trustatlas"].values["Active"] == "Active"
    assert [(c.field_name, c.value) for c in board.writes[0]] == [("Active", "Active")]
    assert claimant.activate("trustatlas") is False, "already Active: nothing to write"
    assert len(board.writes) == 1


def test_activate_refuses_a_project_with_no_card(store, clock):
    board = fb.FakeBoard(fb.item(ROW, Status="Backlog"))
    with pytest.raises(L.LeaseError, match="NO_PROJECT_ITEM"):
        fb.claimant(store, board, clock).activate("trustatlas")
    assert board.writes == []


def _planner(monkeypatch, active: set[str], blocked_even_if_active: bool = False):
    """A planner whose only rule is the Active one, plus optionally a plan-directory refusal."""
    monkeypatch.setattr(planner, "active_projects", lambda: set(active))

    def ready_rows(act, project="", limit=10, root=None, leased=frozenset()):
        if blocked_even_if_active:
            return {
                "ready": [],
                "skipped": {"plan directory: the deliverable is a document": 1},
            }
        if project in act:
            return {"ready": [{"row_id": "ta.suite_green"}], "skipped": {}}
        return {"ready": [], "skipped": {"project not Active": 1}}

    monkeypatch.setattr(planner, "ready_rows", ready_rows)


def test_a_row_held_back_only_by_inactivity_activates_its_project(monkeypatch):
    _planner(monkeypatch, active=set())
    called: list[str] = []
    assert (
        L._ready_from_planner("trustatlas", "ta.suite_green", activate=called.append)
        == ""
    )
    assert called == ["trustatlas"]


def test_without_an_activator_inactivity_still_refuses(monkeypatch):
    _planner(monkeypatch, active=set())
    why = L._ready_from_planner("trustatlas", "ta.suite_green")
    assert "project not Active" in why


def test_any_other_obstacle_refuses_and_does_not_activate(monkeypatch):
    _planner(monkeypatch, active=set(), blocked_even_if_active=True)
    called: list[str] = []
    why = L._ready_from_planner("trustatlas", "ta.suite_green", activate=called.append)
    assert "plan directory" in why and called == []


def test_a_third_active_project_is_allowed_and_noted(monkeypatch, capsys):
    _planner(monkeypatch, active={"pkix", "certledger"})
    called: list[str] = []
    assert (
        L._ready_from_planner("trustatlas", "ta.suite_green", activate=called.append)
        == ""
    )
    assert called == ["trustatlas"]
    assert "at most two" in capsys.readouterr().err


def test_the_command_line_acquire_activates_then_claims(
    store, clock, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PROJECTHUB_LEASES_DB", str(tmp_path / "cli.sqlite"))
    board = fb.FakeBoard(card("trustatlas"), fb.item(ROW, Status="Backlog"))
    monkeypatch.setattr(L, "board_client", lambda: (board, ""))
    from projecthub.github import claims as claims_module

    monkeypatch.setattr(claims_module, "SETTLE_S", 0.5)
    active: set[str] = set()
    monkeypatch.setattr(planner, "active_projects", lambda: set(active))

    def ready_rows(act, project="", limit=10, root=None, leased=frozenset()):
        if project in act:
            return {"ready": [{"row_id": "ta.suite_green"}], "skipped": {}}
        return {"ready": [], "skipped": {"project not Active": 1}}

    monkeypatch.setattr(planner, "ready_rows", ready_rows)
    assert L.main(["acquire", "trustatlas", "ta.suite_green", "--holder", "h"]) == 0
    assert board.by_id["PVTI-trustatlas"].values["Active"] == "Active"
    claimed = board.by_id["PVTI-[trustatlas]-ta.suite_green"].values
    assert claimed["Status"] == "In progress"


def test_a_row_missing_from_the_board_never_activates_its_project(store, clock):
    board = fb.FakeBoard(card("trustatlas"))
    with pytest.raises(L.LeaseError, match="NO_ITEM"):
        fb.claimant(store, board, clock).activate("trustatlas", "ta.suite_green")
    assert "Active" not in board.by_id["PVTI-trustatlas"].values and board.writes == []


def test_a_row_that_is_not_claimable_never_activates_its_project(store, clock):
    board = fb.FakeBoard(card("trustatlas"), fb.item(ROW, Status="Done"))
    with pytest.raises(L.LeaseError, match="NOT_CLAIMABLE"):
        fb.claimant(store, board, clock).activate("trustatlas", "ta.suite_green")
    assert board.writes == []


def test_a_claimable_row_activates_its_project(store, clock):
    board = fb.FakeBoard(card("trustatlas"), fb.item(ROW, Status="Backlog"))
    assert (
        fb.claimant(store, board, clock).activate("trustatlas", "ta.suite_green")
        is True
    )
    assert board.by_id["PVTI-trustatlas"].values["Active"] == "Active"
