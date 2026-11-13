"""The name check's one-word verdict: FREE on PyPI is not enough, a live one-keystroke neighbour refuses it.

PORTFOLIO.md section 10 (2026-11-07) rejected `zimlib`, `zimdoc`, `zimdocs` and `zimdex` for exactly that, and
`names --index` printed FREE beside a neighbour note that agents read past. Nothing here touches the network.
"""

from __future__ import annotations

import pytest

from projecthub.shells import names


def test_free_name_with_live_neighbour_is_not_free():
    live = names.live_neighbours("ieeelib", {"ieeebib", "unrelated"})
    assert live == ["ieeebib"]
    assert names.overall("FREE", live) == "NEIGHBOUR"


def test_taken_name_is_taken():
    assert names.overall("TAKEN", []) == "TAKEN"
    assert names.overall("TAKEN", ["whatever"]) == "TAKEN"


def test_free_name_with_no_neighbour_is_free():
    live = names.live_neighbours("posixlib", {"zzzz"})
    assert live == []
    assert names.overall("FREE", live) == "FREE"


def test_free_without_a_snapshot_is_not_called_free():
    assert names.overall("FREE", None) == "FREE-UNCHECKED"


@pytest.mark.parametrize(
    "reason", ["RESERVED-EMPTY", "ERROR", "RATE-LIMITED", "UNKNOWN"]
)
def test_a_name_that_is_not_free_keeps_its_reason(reason):
    assert names.overall(reason, []) == reason


def test_name_with_uppercase_underscore_and_dots_is_normalised_pep503():
    namespace = {"ieee-bib"}
    assert names.live_neighbours("Ieee_Lib", namespace) == names.live_neighbours(
        "ieee-lib", namespace
    )
    assert names.live_neighbours("Ieee_Lib", namespace) == ["ieee-bib"]
    assert names.live_neighbours("Ieee.._Lib", namespace) == ["ieee-bib"]


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_and_whitespace_names_are_refused(bad):
    with pytest.raises(SystemExit):
        names.main([bad])
