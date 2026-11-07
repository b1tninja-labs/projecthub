"""The loop's selector: its ordering, the two-project rule, and the things it refuses to decide.

Nothing here touches the network or a token. The board is invented cards, exactly as test_projects_mcp.py
does it, and the two IO seams (a project's matrix rows, and deps.py) are stubbed. That is deliberate: the
policy under test is which row is next, and a test that needed the live board would be testing GitHub.
"""

from __future__ import annotations

import pathlib

import ghprojects as g
import loop


def card(title: str, repo: str = "pkix", **values: object) -> g.Item:
    url = f"https://github.com/b1tninja/{repo}/issues/{len(title)}"
    return g.Item(f"I-{title}", "ISSUE", f"N-{title}", url, title, dict(values))


def project(name: str, *, active: bool = False, state: str = "Shell") -> g.Item:
    values: dict[str, object] = {"Level": "Project", "State": state}
    if active:
        values["Active"] = "Active"
    return card(f"{name}: work", repo="sdk-forge", **values)


def row(name: str, status: str, repo: str, **values: object) -> g.Item:
    return card(name, repo=repo, Level="Obligation", Status=status, **values)


def file_rows(*pairs: tuple[str, str]) -> object:
    """A stand-in for planner.next_actions: the matrix's open rows as the file states them."""

    def read(_project: str) -> list[dict[str, str]]:
        return [
            {
                "row": name,
                "status": status,
                "spec": "RFC 2578",
                "milestone": "M1",
                "named_test": "tests/test_x.py::t",
                "evidence": "",
            }
            for name, status in pairs
        ]

    return read


def no_deps(_root: pathlib.Path, _project: str) -> tuple[list[str], list[str]]:
    return [], []


def pick(
    cards: list[g.Item],
    *,
    rows: object = None,
    deps: object = no_deps,
    prefer: str = "",
) -> dict:
    """Run the selector over invented cards, with the disk stubbed out."""
    original = loop.blockings
    loop.blockings = deps  # type: ignore[assignment]
    try:
        return loop.pick(
            pathlib.Path("D:/code"),
            cards,
            prefer=prefer,
            file_rows=rows or file_rows(("anchors.load_anchor()", "[ ]")),
            matrix_for=lambda name: pathlib.Path(f"{name}/ROADMAP.md"),
        )
    finally:
        loop.blockings = original  # type: ignore[assignment]


def test_an_in_progress_row_is_continued_before_a_ready_one_is_started():
    cards = [
        project("pkix", active=True),
        row("chain.candidates()", "In progress", "pkix"),
        row("anchors.load_anchor()", "Ready", "pkix"),
    ]
    got = pick(
        cards,
        rows=file_rows(("chain.candidates()", "[ ]"), ("anchors.load_anchor()", "[ ]")),
    )
    assert got["row"] == "chain.candidates()", (
        "a half-finished row is the cheaper context"
    )
    assert got["project"] == "pkix"
    assert got["promote"] is False and got["needs"] == ""


def test_a_project_outside_the_active_two_is_proposed_never_taken():
    cards = [project("pkix", active=True), row("compliance.set()", "Ready", "ctspy")]
    got = pick(cards, rows=file_rows(("compliance.set()", "[ ]")))
    assert got["project"] == "ctspy"
    assert got["promote"] is True, "one slot is free, so this is a proposal"
    assert "Active" in got["needs"], (
        "the board's readme reserves that field for a person"
    )
    assert got["command"], "a proposal still names the run"


def test_both_slots_used_refuses_a_third_project_and_says_why():
    cards = [
        project("pkix", active=True),
        project("ctspy", active=True),
        row("ocsp.check()", "Ready", "revokekit"),
    ]
    got = pick(cards)
    assert "stop" in got and "command" not in got
    assert got["active_rule"] == "2 of 2 active slots used"
    assert got["skipped"][0]["project"] == "revokekit"
    assert "only a person sets Active" in got["skipped"][0]["why"]


def test_a_dependency_that_is_only_a_plan_withholds_the_row():
    got = pick(
        [project("miblang"), row("catalogue.modules_of()", "Ready", "miblang")],
        deps=lambda root, name: (
            ["itulib"],
            ["hard dependency itulib is plan only on disk"],
        ),
    )
    assert "stop" in got, (
        "a dependency the plan says does not exist must stop the row, not qualify it"
    )
    assert "plan only" in got["skipped"][0]["why"]


def test_a_shell_dependency_is_a_caveat_not_a_block():
    got = pick(
        [project("ctspy", active=True), row("sth.verify()", "Ready", "ctspy")],
        deps=lambda root, name: (
            [],
            ["hard dependency asn1core is a shell: it exists and does nothing yet"],
        ),
    )
    assert got["row"] == "sth.verify()", (
        "whether a shell supplies an edge's capability is a person's call"
    )
    assert any("shell" in note for note in got["caveats"])


def test_an_open_decision_naming_the_project_blocks_its_rows():
    cards = [
        project("ctspy", active=True),
        card(
            "Decision: ctspy log pinning",
            repo="sdk-forge",
            Level="Decision",
            Status="Backlog",
        ),
        row("sth.verify()", "Ready", "ctspy"),
    ]
    got = pick(cards)
    assert "stop" in got, "board.py treats an open decision as the block that it is"
    assert "open decision" in got["skipped"][0]["why"]


def test_the_file_is_the_authority_and_a_stale_card_is_reported():
    cards = [
        project("pkix", active=True),
        row("gone.removed()", "Ready", "pkix", **{"Spec": "RFC 5280 §6"}),
    ]
    got = pick(cards, rows=file_rows(("something.else()", "[ ]")))
    assert got["row"] == "gone.removed()"
    assert got["file_status"] == "", (
        "the file has no such open row, and the step says so instead of inventing one"
    )
    assert got["spec"] == "RFC 5280 §6", (
        "with nothing in the matrix the card's own cell is all there is"
    )
    assert any("no open row of that name" in note for note in got["caveats"]), (
        "board and file disagreeing is drift: say so rather than quietly pick a winner"
    )


def test_the_matrix_outranks_the_card_when_both_name_a_spec():
    cards = [
        project("pkix", active=True),
        row(
            "chain.candidates()",
            "Ready",
            "pkix",
            **{"Spec": "RFC 5280 §6", "Milestone": "M9"},
        ),
    ]
    got = pick(cards, rows=file_rows(("chain.candidates()", "[part]")))
    assert got["spec"] == "RFC 2578", (
        "the matrix is what CI can enforce, so it wins the disagreement"
    )
    assert got["milestone"] == "M1"
    assert got["file_status"] == "[part]", (
        "the board said Ready while the row is part-implemented"
    )


def test_a_preferred_project_is_chosen_first():
    cards = [
        project("pkix", active=True),
        project("ctspy", active=True),
        row("a.one()", "Ready", "pkix"),
        row("b.two()", "In progress", "ctspy"),
    ]
    got = pick(
        cards, rows=file_rows(("a.one()", "[ ]"), ("b.two()", "[ ]")), prefer="pkix"
    )
    assert got["project"] == "pkix", (
        "--project restricts the run without overriding the rule"
    )


def test_the_command_runs_one_row_in_a_worktree_and_bounds_itself():
    command = loop.command_for("anchors.load_anchor()", pathlib.Path("D:/code/pkix"))
    assert "--worktree" in command, "not in a tree another agent may be committing in"
    assert "/implement-obligation anchors.load_anchor()" in command, "one row per run"
    assert (
        "--retries 3" in command and "--timeout 1800" in command and "--json" in command
    )
    assert 'cline -c "D:/code/pkix"' in command


def test_a_backlog_row_inside_an_active_project_is_offered():
    got = pick(
        [
            project("pkix", active=True),
            row("[pkix] chain.candidates: RFC 5280 §6", "Backlog", "pkix"),
        ],
        rows=file_rows(("chain.candidates", "[ ]")),
    )
    assert got["row"] == "chain.candidates", (
        "board.py never moves Status, so Backlog inside Active is the offer"
    )
    assert got["promote"] is False


def test_a_backlog_row_outside_an_active_project_is_not_offered():
    got = pick([project("pkix", active=True), row("x.y()", "Backlog", "revokekit")])
    assert "stop" in got
    assert got["stop"].startswith("no card is Ready or In progress")


def test_nothing_active_is_a_stop_that_names_the_person_action():
    got = pick([project("pkix"), row("x.y()", "Backlog", "pkix")])
    assert "nothing is Active" in got["stop"] and "Active" in got["stop"]
    assert got["backlog_rows"] == 1


def test_the_live_card_shape_joins_to_the_live_row_id():
    """Titles are `[certledger] cl.multi-path: Model I7`; the matrix calls the row `cl.multi-path`.

    The unit fixtures above use bare titles, which is what first shipped this file and what the board does not
    do. This is the test that would have caught it.
    """
    cards = [
        project("certledger", active=True),
        row("[certledger] cl.multi-path: Model I7", "Ready", "certledger"),
    ]
    got = pick(cards, rows=file_rows(("cl.multi-path", "[ ]")))
    assert got["row"] == "cl.multi-path", "the join is the row id, not the whole title"
    assert got["project"] == "certledger"
    assert got["spec"] == "RFC 2578", "the matrix cell wins over the title's copy"
    assert got["caveats"] == []


def test_a_card_whose_title_and_url_disagree_about_the_project_says_so():
    cards = [
        project("pkix", active=True),
        row("[certledger] a.b: spec", "Ready", "pkix"),
    ]
    got = pick(cards, rows=file_rows(("a.b", "[ ]")))
    assert any("disagrees with itself" in note for note in got["caveats"])


def test_no_ready_card_at_all_is_a_stop_not_an_empty_step():
    got = pick([project("pkix", active=True), row("x.y()", "Blocked", "pkix")])
    assert got["stop"].startswith("no card is Ready or In progress")
    assert got["ready_cards"] == []


def test_repo_of_reads_the_card_url_and_says_so_when_there_is_none():
    assert loop.repo_of(card("a.b()", repo="pkix")) == "b1tninja/pkix"
    assert (
        loop.repo_of(g.Item("I", "DRAFT", "N", "https://github.com/x", "draft", {}))
        == ""
    )
