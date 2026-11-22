"""An issue title never exceeds GitHub's 256-character limit, keeps "[project] row" whole and is deterministic."""

from __future__ import annotations

from projecthub.github import sync


def row(title: str, spec: str) -> dict[str, str]:
    return {"title": title, "spec": spec, "status": "[ ]"}


def test_a_short_title_is_unchanged():
    assert sync.issue_title("pkix", row("a.b", "spec"), "") == "[pkix] a.b: spec"


def test_a_long_title_is_cut_to_the_limit_and_keeps_its_row():
    long = "x" * 600
    got = sync.issue_title("pkix", row("tests.asn1core_build", long), "")
    assert len(got) == sync.TITLE_MAX
    assert got.startswith("[pkix] tests.asn1core_build: ") and got.endswith("…")
    assert got == sync.issue_title("pkix", row("tests.asn1core_build", long), ""), (
        "deterministic"
    )


def test_an_overlong_row_id_is_still_capped():
    got = sync.issue_title("pkix", row("r" * 300, "spec"), "")
    assert len(got) <= sync.TITLE_MAX


def test_a_long_spec_is_clipped_for_the_board_text_field():
    from projecthub.github import board

    values = board.row_values(
        {"spec": "s" * 5000, "status": "[ ]"}, 1, 1, "tests/t.py::test_x"
    )
    assert len(values["Spec"]) == board.TEXT_MAX and values["Spec"].endswith("…")
    assert values == board.row_values(
        {"spec": "s" * 5000, "status": "[ ]"}, 1, 1, "tests/t.py::test_x"
    )
    assert board.row_values({"spec": "short", "status": "[ ]"}, 1, 1)["Spec"] == "short"
