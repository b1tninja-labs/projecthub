"""Tests for the tool that decides which obligations become work items.

The forge had no tests until now, which is the same defect it exists to prevent elsewhere: sync_roadmap
files issues into eighteen repositories, and a parser that agrees with itself would file a confident,
wrong board. These run without the network and without a token.

This file replaces an inline heredoc in .github/workflows/ci.yml that asserted the same things. Having
two copies of one assertion is not a joke in this portfolio: a fix landed in one copy and the other
went red in CI on the same commit.
"""

from __future__ import annotations

import sync_roadmap as s

PKIX_ROW = "| `anchors.load_anchor()` | RFC 5280 sec. 6.1 | `[ ]` | `tests/test_obligations.py::load_anchor` |"
CERTLEDGER_ROW = "| `cl.ambient-trust` | Model I1 | `[x]` | `anchors.py`, `context.py` | `test_environment_is_ignored` |"
NOT_A_ROW = "| Non-goal | Status | Revisit when |\n| a certificate parser | `[no]` | a named document requires it |"


def test_pkix_shape_parses():
    rows = s.rows_of(PKIX_ROW)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["name"] == "anchors.load_anchor"
    assert row["title"] == "anchors.load_anchor()"
    assert row["status"] == "[ ]"
    assert s.STATUSES[row["status"]][1] == "open"
    assert row["evidence"] == ""


def test_certledger_shape_parses_including_evidence():
    rows = s.rows_of(CERTLEDGER_ROW)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["name"] == "cl.ambient-trust"
    # Markdown cells keep their backticks; the issue body renders them, and stripping them here would
    # silently mangle evidence strings that contain punctuation.
    assert row["evidence"] == "`test_environment_is_ignored`"
    assert s.STATUSES[row["status"]][1] == "close", "a done row must not open an issue"


def test_headers_and_non_goals_are_not_rows():
    assert s.rows_of(NOT_A_ROW) == []
    assert s.rows_of("") == []


def test_milestones_come_from_the_ladder_only_where_cited():
    text = "- M7 vendor program containers: CAB member extraction\n- not a milestone\n"
    assert s.milestones_of(text) == {"M7": "vendor program containers"}
    assert (
        s.milestone_of(["MS-CAB (vendor format, not an RFC)", "`tests/t.py::x`"]) == ""
    )
    assert s.milestone_of(["MS-CAB (M7)"]) == "M7"


def test_marker_round_trips_through_the_issue_body():
    row = s.rows_of(PKIX_ROW)[0]
    body = s.body(row, "M1")
    assert s.MARKER.format(row["name"]) in body
    assert "tests/test_obligations.py::load_anchor" in body
    # A done row's body is never used, but an open row's must carry the rule that closes it.
    assert "named test" in body.lower()
