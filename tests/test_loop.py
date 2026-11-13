"""The loop's selector: its ordering, the two-project rule, and the things it refuses to decide.

Nothing here touches the network or a token. The board is invented cards, exactly as test_projects_mcp.py
does it, and the two IO seams (a project's matrix rows, and deps.py) are stubbed. That is deliberate: the
policy under test is which row is next, and a test that needed the live board would be testing GitHub.

One seam is deliberately not stubbed: blockings() is also called against the real deps.py, because stubbing it
everywhere is what let a wrong field name survive there. A stub cannot catch a misspelled attribute.
"""

from __future__ import annotations

import pathlib

import pytest
import types

from projecthub import deps
from projecthub.github import client as g
from projecthub.mcp import loop


def card(title: str, repo: str = "pkix", **values: object) -> g.Item:
    url = f"https://github.com/b1tninja/{repo}/issues/{len(title)}"
    return g.Item(f"I-{title}", "ISSUE", f"N-{title}", url, title, dict(values))


def project(name: str, *, active: bool = False, state: str = "Shell") -> g.Item:
    values: dict[str, object] = {"Level": "Project", "State": state}
    if active:
        values["Active"] = "Active"
    return card(f"{name}: work", repo="portfolio", **values)


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
    runbook: object = None,
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
            runbook=runbook,  # type: ignore[arg-type]
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
            ["primarysource"],
            ["hard dependency primarysource is plan only on disk"],
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
            repo="portfolio",
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


def test_a_row_id_with_parentheses_and_truncated_prose_still_joins():
    """The live pkix shape, which the certledger shape above did not teach.

    Titles arrive as "[pkix] anchors.load_anchor(): trust anchor selection, including name restrictions and
    ...": the id carries parentheses, and the projector truncates the prose with an ellipsis. The first
    version of the parser had no parentheses in its id class, so card_row() fell back to the raw title and the
    emitted prompt named a whole sentence instead of a row. Running the selector against the live board caught
    it; the fixtures could not, because they were written from a guess about the shape.
    """
    title = "[pkix] anchors.load_anchor(): trust anchor selection, including name restrictions and ..."
    cards = [project("pkix", active=True), row(title, "Backlog", "pkix")]
    # The file side is spelled the way planner.next_actions actually returns it - without the call marker -
    # because that disagreement between the two sources is real, and surviving it is the join's job.
    got = pick(cards, rows=file_rows(("anchors.load_anchor", "[ ]")))
    assert got["row"] == "anchors.load_anchor()", (
        "parentheses belong to an id on this board"
    )
    assert got["file_status"] == "[ ]"
    assert got["named_test"] == "tests/test_x.py::t", (
        "the join must carry the named test across"
    )
    assert got["caveats"] == [], "a call marker and a truncated title are not drift"
    assert got["prompt"] == "/implement-obligation anchors.load_anchor()"


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


def test_a_project_with_no_runbook_is_withheld_even_when_it_is_active():
    """A command whose own prompt cannot resolve is not the next step.

    This was measured, not reasoned about: with asn1core and pkix both Active, the ranking chose asn1core,
    the run started, and it died on "Unknown command or unquoted prompt" before touching a file, because the
    runbook a slash command expands into ships with the generated shells and not with the phase-1 projects.
    """
    cards = [
        project("asn1core", active=True),
        project("pkix", active=True),
        row("dk.corpus", "Backlog", "asn1core"),
        row("chain.validate()", "Backlog", "pkix"),
    ]
    got = pick(
        cards,
        rows=file_rows(("dk.corpus", "[part]"), ("chain.validate()", "[ ]")),
        runbook=lambda name: "" if name == "pkix" else "no runbook here",
    )
    assert got["project"] == "pkix", "the runnable project, not the merely earlier one"
    assert got["skipped"][0]["project"] == "asn1core"
    assert "no runbook" in got["skipped"][0]["why"], (
        "the reason is printed, not swallowed"
    )


def test_the_step_carries_the_prompt_as_one_field_so_no_driver_parses_quotes():
    cards = [project("pkix", active=True), row("chain.validate()", "Backlog", "pkix")]
    got = pick(
        cards,
        rows=file_rows(("chain.validate()", "[ ]")),
        runbook=lambda name: "",
    )
    assert got["prompt"] == "/implement-obligation chain.validate()"
    assert got["prompt"] in got["command"]


def test_runbook_gap_wants_the_file_not_just_the_directory(tmp_path):
    workflows = tmp_path / "asn1core" / ".cline" / "workflows"
    workflows.mkdir(parents=True)
    assert loop.runbook_gap(tmp_path, "asn1core"), (
        "an empty workflows directory runs nothing"
    )
    (workflows / "implement-obligation.md").write_text("# steps\n", encoding="utf-8")
    assert loop.runbook_gap(tmp_path, "asn1core") == ""
    assert "asn1kit" in loop.runbook_gap(tmp_path, "asn1kit")


def test_blockings_reads_the_real_dependency_graph():
    """The regression: blockings() once read `edge.name`, and deps.Edge has no such field.

    Every other test stubs deps.py, and every project the selector had actually chosen (asn1core) has zero
    edges, so the loop body never ran and the AttributeError stayed hidden. pkix is the first chosen project
    with a dependency, and it has one: asn1core, whose state is real code. Had this test existed, the selector
    would have crashed in front of a test instead of in front of a run.
    """
    root = pathlib.Path("D:/code")
    if not (root / "pkix" / "docs" / "plan.md").is_file():
        pytest.skip("needs the D:/code workspace with pkix checked out")
    hard, notes = loop.blockings(root, "pkix")
    assert [(e.project, e.dependency) for e in deps.edges(root, "pkix")] == [
        ("pkix", "asn1core")
    ], "this test is only worth anything if pkix still has an edge to walk"
    assert hard == [], "asn1core is real code, so it must not withhold a pkix row"
    assert notes == []


def test_blockings_reports_a_dependency_that_is_only_a_shell(monkeypatch):
    monkeypatch.setattr(
        loop.deps, "edges", lambda root, name: [deps.Edge("pkix", "asn1core", False)]
    )
    monkeypatch.setattr(
        loop.deps,
        "dependency_state",
        lambda root, name: types.SimpleNamespace(state="shell"),
    )
    _hard, notes = loop.blockings(pathlib.Path("D:/code"), "pkix")
    assert notes == [
        "hard dependency asn1core is a shell: it exists and does nothing yet"
    ]

    monkeypatch.setattr(
        loop.deps,
        "dependency_state",
        lambda root, name: types.SimpleNamespace(state="plan only"),
    )
    hard, notes = loop.blockings(pathlib.Path("D:/code"), "pkix")
    assert hard == ["asn1core"], "plan only is a hard block, per the workspace rule"
    assert notes == ["hard dependency asn1core is plan only on disk"]


class Claims:
    """A claimant in memory: the first claim on a key wins, and a later one is refused LEASE_HELD.

    Exclusion in pickup comes from the cards, never from here: `live_keys` and `blocked_keys` are gone
    on purpose, so a pickup that still asked a local store would fail with AttributeError."""

    def __init__(
        self, held: tuple[str, ...] = (), blocked: tuple[str, ...] = ()
    ) -> None:
        self.held = set(held)
        self.blocked = set(blocked)

    def acquire(self, project: str, row: str, who: str) -> types.SimpleNamespace:
        from projecthub.mcp import work

        if f"{project}:{row}" in self.held:
            raise work.LeaseError("LEASE_HELD", f"{project}:{row} is claimed")
        self.held.add(f"{project}:{row}")
        return types.SimpleNamespace(lease_id="L1", expires_at=1_800_000_000.0, who=who)


PACKET = {
    "row_id": "pkix:anchors.load_anchor",
    "status": "[ ]",
    "where": "`tests/test_a.py::t`",
    "stub": {"path": "pkix/anchors.py", "signature": "load_anchor(der)"},
    "named_test": "tests/test_a.py::t",
    "test_state": "to be written",
    "spec": "RFC 5280 section 6.1",
    "spec_note": "Read the clause with rfclib.",
    "acceptance": {"done_when": "the row reads [ok]", "gates": "python -m pytest -q"},
    "depends_on": [{"project": "asn1core", "state": "Implemented"}],
    "open_decisions": [],
    "notes": ["a note"],
}


def pickup(
    cards: list[g.Item], store: Claims, packet: object = None, deps: object = no_deps
) -> tuple[int, str]:
    original = loop.blockings
    loop.blockings = deps  # type: ignore[assignment]
    try:
        return loop.run_pickup(
            pathlib.Path("D:/code"),
            cards,
            store=store,
            make_packet=packet or (lambda key: PACKET),  # type: ignore[arg-type]
            file_rows=file_rows(
                ("anchors.load_anchor()", "[ ]"), ("chain.build()", "[ ]")
            ),  # type: ignore[arg-type]
            matrix_for=lambda name: pathlib.Path(f"{name}/ROADMAP.md"),
        )
    finally:
        loop.blockings = original  # type: ignore[assignment]


BOARD_CARDS = [
    project("pkix", active=True),
    row("anchors.load_anchor()", "Ready", "pkix"),
    row("chain.build()", "Backlog", "pkix"),
]


def test_pickup_claims_one_row_and_its_output_is_the_assignment():
    store = Claims()
    code, text = pickup(BOARD_CARDS, store)
    assert code == 0
    assert store.held == {"pkix:anchors.load_anchor"}, (
        "the script claims it, so the agent has no choice to make"
    )
    assert text.startswith("# Your assignment: pkix:anchors.load_anchor")
    assert "work only this row" in text and "L1" in text
    assert "Say done, fixed or tested only when a tool's output shows it." in text
    assert "pr_opened" in text and "decision_needed" in text


def test_two_arrivals_never_get_the_same_row():
    store = Claims()
    first = pickup(BOARD_CARDS, store)[1]
    second = pickup(BOARD_CARDS, store)
    assert "anchors.load_anchor" in first
    assert second[0] == 0 and store.held == {
        "pkix:anchors.load_anchor",
        "pkix:chain.build",
    }


def test_no_work_is_a_distinct_state_that_says_ask():
    store = Claims(("pkix:anchors.load_anchor", "pkix:chain.build"))
    code, text = pickup(BOARD_CARDS, store)
    assert code == loop.NO_WORK_EXIT and text.startswith("NO WORK: ")
    assert "Ask the person what to work on" in text
    assert "another agent holds the claim" in text


def test_a_row_outside_the_active_projects_needs_a_person_and_is_not_claimed():
    store = Claims()
    code, text = pickup(
        [project("pkix", active=True), row("compliance.set()", "Ready", "ctspy")], store
    )
    assert code == loop.NO_WORK_EXIT and "a person sets Active" in text
    assert store.held == set()


def test_a_row_with_no_packet_is_passed_over():
    def packet(key: str) -> dict[str, object]:
        if "anchors" in key:
            raise LookupError("stale card")
        return {**PACKET, "row_id": key}

    store = Claims()
    code, text = pickup(BOARD_CARDS, store, packet=packet)
    assert code == 0 and "pkix:chain.build" in text
    assert store.held == {"pkix:chain.build"}


def test_a_blocked_row_waits_for_a_person():
    store = Claims()
    cards = [
        project("pkix", active=True),
        row("anchors.load_anchor()", "Blocked", "pkix"),
        row("chain.build()", "Backlog", "pkix"),
    ]
    code, text = pickup(cards, store)
    assert code == 0
    assert store.held == {"pkix:chain.build"}, "Blocked on the Project is not startable"


def test_a_card_in_the_shared_repository_belongs_to_its_title_prefix(monkeypatch):
    import projecthub.github.sync as sync_module

    monkeypatch.setattr(sync_module, "ISSUE_REPO", "b1tninja-labs/portfolio")
    moved = g.Item(
        "I",
        "ISSUE",
        "N",
        "https://github.com/b1tninja-labs/portfolio/issues/9",
        "[pkix] a.b(): x",
        {},
    )
    assert loop.project_of(moved) == "pkix"
    assert loop.project_of(card("no prefix", repo="ctspy")) == "ctspy"


@pytest.fixture(autouse=True)
def _one_repository_per_project(monkeypatch):
    """These tests describe the per-project layout. The shared repository has tests of its own."""
    import projecthub.github.sync as sync_module

    monkeypatch.setattr(sync_module, "ISSUE_REPO", "")


class BusyClaims(Claims):
    """A store whose caller already holds its one live lease."""

    def acquire(self, project: str, row: str, who: str) -> types.SimpleNamespace:
        from projecthub.mcp import work

        raise work.LeaseError("HOLDER_BUSY", f"{who} already holds pkix:other")


def test_a_holder_with_a_live_lease_is_told_so_rather_than_given_a_traceback():
    code, text = pickup(BOARD_CARDS, BusyClaims())
    assert code == loop.BUSY_EXIT
    assert text.startswith("BUSY: HOLDER_BUSY") and "park" in text
