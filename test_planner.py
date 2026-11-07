"""The guard is the point. Everything else in planner.py is a convenience read.

These tests do not touch the network or the cloud. They use a row shape pointed at a real file in pkix
(`tests/test_roadmap_rows.py`) and a project name that cannot exist, so the outcomes depend on nothing but
the guard's own logic.
"""

from __future__ import annotations

import pytest

import planner as p

REAL_TEST_ROW = "| `x.y()` | RFC 5280 | `[ ]` | `tests/test_roadmap_rows.py::test_every_row_names_a_real_callable` |\n"
PHANTOM_ROW = (
    "| `a.b()` | RFC 5280 | `[ ]` | `tests/test_obligations.py::load_anchor` |\n"
)
NONGOAL_ROW = "| `crypto.parse()` | design | `[no]` | `tests/test_roadmap_rows.py::test_matrix_has_rows` |\n"


def test_open_row_can_move_to_partial():
    updated, refusal = p.rewrite_row(PHANTOM_ROW, "pkix", "a.b", "[part]")
    assert refusal == "", refusal
    assert "`[part]`" in updated


def test_done_is_refused_when_the_named_test_file_is_absent():
    updated, refusal = p.rewrite_row(PHANTOM_ROW, "no-such-project", "a.b", "[ok]")
    assert "REFUSED" not in refusal  # rewrite_row returns the refusal separately
    assert refusal and "does not exist" in refusal
    assert updated == PHANTOM_ROW


def test_done_is_refused_when_the_symbol_is_absent():
    _, refusal = p.rewrite_row(
        "| `x.y()` | s | `[ ]` | `tests/test_roadmap_rows.py::test_nobody_wrote_this` |\n",
        "pkix",
        "x.y",
        "[ok]",
    )
    assert "defines no test_nobody_wrote_this" in refusal


def test_done_is_permitted_when_the_test_file_defines_the_symbol():
    updated, refusal = p.rewrite_row(REAL_TEST_ROW, "pkix", "x.y", "[x]")
    assert refusal == "", refusal
    assert "`[x]`" in updated


def test_a_declared_non_goal_is_not_a_status_to_flip():
    _, refusal = p.rewrite_row(NONGOAL_ROW, "pkix", "crypto.parse", "[ok]")
    assert "non-goal" in refusal


def test_unknown_status_and_unknown_row_are_rejected():
    assert "not a status" in p.rewrite_row(PHANTOM_ROW, "pkix", "a.b", "done!")[1]
    assert "no row named" in p.rewrite_row(PHANTOM_ROW, "pkix", "q.r", "[part]")[1]


def test_evidence_reports_the_file_it_checked():
    ok, why = p.test_evidence(
        "pkix", "`tests/test_roadmap_rows.py::test_matrix_has_rows`"
    )
    assert ok and why == "tests/test_roadmap_rows.py defines test_matrix_has_rows"


def test_mcp_surface_exposes_exactly_five_tools():
    pytest.importorskip("mcp")
    import anyio

    listed = anyio.run(p.build().list_tools)
    names = sorted(t.name for t in getattr(listed, "tools", listed))
    assert len(names) == 5, names
    assert all(n.startswith("forge_") for n in names), names
