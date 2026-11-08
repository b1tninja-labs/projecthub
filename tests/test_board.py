"""The board's decisions, tested without the network.

What these pin: renaming the pre-made Status options keeps every card's value; nothing a person moved is
moved back; every view is filtered by Level so the grains never mix; a project card says plainly when there
is no repository and what blocks it; a wave quotes its exit test.
"""

from __future__ import annotations

import dataclasses
import pathlib

import pytest

from planforge.github import board as b
from planforge.github import client as g
from planforge import registry as p


def test_status_options_keep_ids_through_a_rename():
    wanted = b.options(b.STATUS, {"Todo": "t1", "In Progress": "p1", "Done": "d1"})
    by_name = {o["name"]: o for o in wanted}
    assert by_name["Backlog"]["id"] == "t1"
    assert by_name["In progress"]["id"] == "p1"
    assert "id" not in by_name["Blocked"]
    assert all(o["description"] for o in wanted)


def test_cycles_are_dated_and_contiguous():
    got = b.cycles("2026-11-09", 14, 3)
    assert [c["startDate"] for c in got] == ["2026-11-09", "2026-11-23", "2026-12-07"]


def test_priority_follows_tier_and_untiered_gets_none():
    assert [b.priority(t) for t in (1, 2, 5, 0)] == ["P0", "P1", "P4", ""]


def test_every_view_is_filtered_and_built_from_real_field_ids():
    names = [
        "Status",
        "Repository",
        "Priority",
        "Wave",
        "Title",
        "Level",
        "Kind",
        "State",
    ]
    ids = {n: i for i, n in enumerate(names, 1)}
    specs = b.view_specs(ids)
    assert [s["name"] for s in specs][:4] == [
        "Portfolio",
        "Waves",
        "Decisions",
        "Work board",
    ]
    for spec in specs:
        assert spec.get("filter"), f"{spec['name']} would mix projects, waves and rows"
        for key in ("visible_fields", "group_by", "vertical_group_by"):
            assert all(isinstance(v, int) for v in spec.get(key, []))
    assert specs[0]["filter"] == "level:Project" and specs[0]["group_by"] == [3]


ENTRY = p.Entry(
    "pkix", "Library", 2, 2, "pkix", "", "forge.POSITION", ("asn1core", "rpkit")
)
STATE = p.State(
    "Shell", 12, 0, "ROADMAP.md", "", "Certification path validation per RFC 5280."
)


def test_a_project_card_says_there_is_no_repository_and_what_blocks_it():
    body = b.project_body(
        ENTRY,
        STATE,
        [("asn1core", "Implemented"), ("rpkit", "Shell")],
        [(2, "certledger as a flagship service")],
        [{"name": "a.b", "title": "a.b()", "status": "[ ]", "milestone": "M1"}],
        {},
    )
    assert "<!-- portfolio:project:pkix -->" in body
    assert "local only - no GitHub repository yet" in body
    assert "- `asn1core`: Implemented\n" in body
    assert "- `rpkit`: Shell - **blocks this**" in body
    assert "Decision 2: certledger as a flagship service" in body
    assert "| `a.b()` | `[ ]` | M1 |" in body
    assert "0 of 12 obligations done" in body


def test_a_project_title_prefers_the_portfolio_wording():
    what = "a public register of demonstrated key compromise"
    named = p.Entry("keyproof", "Plan", 1, 3, "keyproof", what, "s4")
    assert b.project_title(named, STATE) == f"keyproof: {what}"
    assert (
        b.project_title(ENTRY, STATE)
        == "pkix: Certification path validation per RFC 5280"
    )


def test_a_wave_quotes_its_exit_test_and_lists_its_projects():
    wave = p.Wave(
        2,
        "first real answer",
        "prose",
        "the graph reconciles against the signed tree size",
    )
    body = b.wave_body(wave, [(ENTRY, STATE)])
    assert "> the graph reconciles against the signed tree size" in body
    assert "- `pkix` - Shell, 0/12 rows" in body
    assert b.wave_title(wave) == "Wave 2: first real answer"
    assert "None stated" in b.wave_body(p.Wave(5, "the rest", "", ""), [])


def project_fields() -> g.Project:
    names = ["Status", "Level", "Rows done", "Spec"]
    return g.Project(
        "P", 4, "board", False, fields={n: g.Field(n, n, "TEXT") for n in names}
    )


def test_changes_never_move_a_card_a_person_moved_but_do_mark_done():
    items = {
        "N1": g.Item("I1", "ISSUE", "N1", "", "", {"Status": "In progress"}),
        "N2": g.Item("I2", "ISSUE", "N2", "", "", {"Status": "In progress"}),
        "N3": g.Item("I3", "ISSUE", "N3", "", "", {}),
    }
    wanted: dict[str, dict[str, object]] = {
        "N1": {"Status": "Backlog"},
        "N2": {"Status": "Done"},
        "N3": {"Status": "Backlog", "Level": "Obligation"},
    }
    got = {
        (c.item_id, c.field_name): c.value
        for c in b.changes_for(project_fields(), items, wanted)
    }
    assert ("I1", "Status") not in got
    assert got[("I2", "Status")] == "Done"
    assert got[("I3", "Status")] == "Backlog" and got[("I3", "Level")] == "Obligation"


def test_changes_are_idempotent_including_numbers():
    current = {"Status": "Backlog", "Rows done": 6.0, "Spec": "RFC 1"}
    items = {"N1": g.Item("I1", "ISSUE", "N1", "", "", current)}
    wanted: dict[str, dict[str, object]] = {
        "N1": {"Status": "Backlog", "Rows done": 6, "Spec": "RFC 1", "Level": None}
    }
    assert b.changes_for(project_fields(), items, wanted) == []


def test_row_values_take_the_node_id_and_mark_done_rows():
    row = {
        "name": "a.b",
        "spec": "RFC 1",
        "where": "`t.py::x` — plus two sentences of evidence nobody asked the board to hold",
        "status": "[ok]",
    }
    values = b.row_values(row, 2, 2, "t.py::x")
    assert values["Named test"] == "t.py::x" and values["Status"] == "Done"
    assert values["Priority"] == "P1" and values["Wave"] == "Wave 2"
    assert b.row_values(row, 0, 0)["Wave"] == ""


def test_row_values_never_put_the_evidence_cell_in_a_board_field():
    # The defect this guards: Named test was built from the Where cell, so a row whose evidence is a paragraph
    # wrote a paragraph into a Projects text column. GitHub refused every one of them ("Column value must be a
    # valid value for text column"), --apply reported the failures, and --check then demanded the same six
    # writes forever because they could never land. A row that names no test names nothing on the board.
    long_where = "tools/verify_provenance.py — " + "measured detail, " * 200
    row = {
        "name": "ta.x",
        "spec": "PLAN.md §chain",
        "where": long_where,
        "status": "[part]",
    }
    values = b.row_values(row, 2, 2, "")
    assert values["Named test"] == ""
    assert values["Status"] == "Backlog"


# -- a whole build against fakes ------------------------------------------------------------------------
#
# Nothing below reaches the network: the board client and the issue store are in-memory fakes that record
# every write, so a test can say exactly what a run touched.


class FakeIssues:
    def __init__(self) -> None:
        self.repos: dict[str, list[b.Issue]] = {}
        self.writes: list[tuple[str, ...]] = []

    def seed(self, repo: str, title: str, body: str, milestone: str = "") -> b.Issue:
        issues = self.repos.setdefault(repo, [])
        n = len(issues) + 1
        url = f"https://github.com/{repo}/issues/{n}"
        issue = b.Issue(n, f"{repo}#{n}", url, title, body, "OPEN", milestone)
        issues.append(issue)
        return issue

    def close(self, repo: str, number: int) -> None:
        old = self.repos[repo][number - 1]
        self.repos[repo][number - 1] = dataclasses.replace(old, state="CLOSED")

    def read(self, repo: str) -> list[b.Issue]:
        return list(self.repos.get(repo, []))

    def create(self, repo: str, title: str, body: str, milestone: str) -> None:
        self.writes.append(("create", repo, title))
        self.seed(repo, title, body, milestone)

    def edit(
        self, repo: str, number: int, title: str, body: str, milestone: str
    ) -> None:
        self.writes.append(("edit", repo, number))
        old = self.repos[repo][number - 1]
        self.repos[repo][number - 1] = dataclasses.replace(
            old, title=title, body=body, milestone=milestone or old.milestone
        )


def full_schema() -> g.Project:
    fields = {
        "Status": g.Field(
            "S", "Status", "SINGLE_SELECT", {n: n for n, _, _ in b.STATUS}
        ),
        "Cycle": g.Field("C", "Cycle", "ITERATION"),
    }
    for name, spec in b.SELECTS.items():
        fields[name] = g.Field(name, name, "SINGLE_SELECT", {n: n for n, _, _ in spec})
    for name in b.NUMBERS:
        fields[name] = g.Field(name, name, "NUMBER")
    for name in b.TEXT_FIELDS:
        fields[name] = g.Field(name, name, "TEXT")
    return g.Project("PRJ", 4, "Protocol SDK portfolio", False, fields=fields)


class FakeClient:
    """The board side. Cards are keyed by issue node id; links by connection, then issue node id."""

    def __init__(self) -> None:
        self.cards: dict[str, g.Item] = {}
        self.links: dict[str, dict[str, set[str]]] = {"subIssues": {}, "blockedBy": {}}
        self.writes: list[tuple[str, ...]] = []
        self.projects = [g.Project("PRJ", 4, "Protocol SDK portfolio", False)]
        self.fail_links = False

    def guard(self, needed: int) -> None:
        pass

    def list_projects(self, owner: str) -> list[g.Project]:
        return self.projects

    def project(self, owner: str, number: int) -> g.Project:
        assert number == 4, "only board #4 exists"
        return full_schema()

    def readme(self, project_id: str) -> tuple[str, str]:
        return b.README, b.SHORT

    def items(self, owner: str, number: int) -> list[g.Item]:
        return list(self.cards.values())

    def add_items(self, project_id: str, ids: list[str]) -> dict[str, str]:
        for node in ids:
            self.writes.append(("add", node))
            repo, _, n = node.partition("#")
            url = f"https://github.com/{repo}/issues/{n}"
            self.cards[node] = g.Item(f"item:{node}", "ISSUE", node, url, node, {})
        return {n: f"item:{n}" for n in ids}

    def set_values(
        self, project: g.Project, changes: list[g.Change]
    ) -> list[g.Outcome]:
        for change in changes:
            node = change.item_id.removeprefix("item:")
            self.writes.append(("set", node, change.field_name))
            self.cards[node].values[change.field_name] = change.value
        return [g.Outcome(c, True) for c in changes]

    def linked_ids(self, issue_id: str, connection: str) -> set[str]:
        return set(self.links[connection].get(issue_id, set()))

    def _link(self, connection: str, issue: str, other: str) -> None:
        self.writes.append((connection, issue, other))
        if self.fail_links:
            raise g.GhProjectsError("refused")
        self.links[connection].setdefault(issue, set()).add(other)

    def add_sub_issue(self, parent: str, child: str) -> None:
        self._link("subIssues", parent, child)

    def add_blocked_by(self, issue: str, blocker: str) -> None:
        self._link("blockedBy", issue, blocker)

    def view_names(self, owner: str, number: int) -> list[str]:
        return [str(s["name"]) for s in b.view_specs({})]

    def rest_field_ids(self, owner: str, number: int) -> dict[str, int]:
        return {}

    def create_view(self, owner: str, number: int, spec: dict[str, object]) -> None:
        self.writes.append(("view", str(spec["name"])))

    def set_options(self, *args: object) -> None:
        self.writes.append(("options",))

    def create_field(self, *args: object, **kwargs: object) -> str:
        self.writes.append(("field", str(args[1])))
        return "F"

    def describe(self, *args: object) -> None:
        self.writes.append(("describe",))


MATRIX = """# ROADMAP

- M1 first rung: loading
  and parsing.

| Obligation | Spec | Status | Where | Milestone |
|---|---|---|---|---|
| `{p}.load()` | RFC 1 s1 | {s} | `tests/test_{p}.py::test_load` (to be written) | M1 |
| `{p}.dump()` | RFC 1 s2 | [ ] | `tests/test_{p}.py::test_dump` (to be written) | M1 |
"""


class World:
    """Two projects with repositories and matrices; alpha depends on beta, one decision names alpha, and
    one wave holds both. Each project has two row issues, as sync_roadmap.py files them."""

    def __init__(self, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        (root / "PORTFOLIO.md").write_text("# portfolio\n", encoding="utf-8")
        self.entries = [
            p.Entry("alpha", "Library", 1, 1, "alpha", "", "s4", ("beta",)),
            p.Entry("beta", "Library", 1, 1, "beta", "", "s4"),
        ]
        self.states = {}
        self.issues, self.client = FakeIssues(), FakeClient()
        for name in ("alpha", "beta"):
            self.write(name, "[ ]")
            self.states[name] = p.State(
                "Shell",
                2,
                0,
                "ROADMAP.md",
                f"b1tninja/{name}",
                f"{name}, see D:/code/{name}/README.md",
            )
            for fn in ("load", "dump"):
                self.issues.seed(
                    f"b1tninja/{name}",
                    f"{name}.{fn}()",
                    f"<!-- roadmap:{name}.{fn} -->",
                )
        decision = p.Decision(1, "`alpha` waits", "body", False, ("alpha",))
        monkeypatch.setattr(b, "ROOT", root)
        monkeypatch.setattr(b.portfolio, "entries", lambda: self.entries)
        monkeypatch.setattr(b.portfolio, "state", lambda _, e: self.states[e.name])
        monkeypatch.setattr(b.portfolio, "decisions", lambda _, __: [decision])
        monkeypatch.setattr(
            b.portfolio, "waves", lambda _: [p.Wave(1, "first", "", "exit")]
        )

    def write(self, name: str, status: str) -> None:
        (self.root / name).mkdir(exist_ok=True)
        (self.root / name / "ROADMAP.md").write_text(
            MATRIX.format(p=name, s=status), encoding="utf-8"
        )

    def run(self, *argv: str) -> int:
        self.issues.writes.clear()
        self.client.writes.clear()
        return b.main(["--number", "4", *argv], client=self.client, issues=self.issues)

    def card(self, name: str) -> b.Issue:
        marker = b.MARK.format("project", name)
        return next(i for i in self.issues.repos[b.PORTFOLIO_REPO] if marker in i.body)

    def writes(self) -> list[tuple[object, ...]]:
        return [*self.issues.writes, *self.client.writes]


def test_a_full_apply_is_idempotent(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    assert world.run("--apply") == 0
    assert world.writes(), "the first run builds the board"
    assert world.run("--apply") == 0
    assert world.writes() == []
    assert world.run("--check") == 0


def test_a_filtered_run_touches_only_its_project_and_is_idempotent(
    tmp_path, monkeypatch
):
    world = World(tmp_path, monkeypatch)
    assert world.run("--apply") == 0
    beta_before = world.card("beta")
    assert "b1tninja/beta/issues/1" in beta_before.body, "beta's card links its rows"

    world.write("alpha", "[ok]")
    world.write(
        "beta", "[ok]"
    )  # beta changed too; a run filtered to alpha must leave it alone
    assert world.run("--apply", "--project", "alpha") == 0
    alpha = world.card("alpha")
    owned = {alpha.node_id} | {i.node_id for i in world.issues.repos["b1tninja/alpha"]}
    touched = world.writes()
    assert touched, "alpha's row went done, so its card and its row card change"
    for write in touched:
        kind = write[0]
        if kind in ("create", "edit"):
            assert write[1] == "b1tninja/alpha" or write[2] == alpha.number, write
        elif kind in ("set", "add"):
            assert write[1] in owned, write
        elif kind in ("subIssues", "blockedBy"):
            assert write[1] in owned or write[2] in owned, write
        else:
            pytest.fail(f"a filtered run changed the board itself: {write}")
    assert world.card("beta") == beta_before, "a run filtered to alpha rewrote beta"

    assert world.run("--apply", "--project", "alpha") == 0
    assert world.writes() == [], "a second filtered run must change nothing"
    assert world.run("--check", "--project", "alpha") == 0
    assert world.run("--check") == 1, "beta's done row is still file-ahead"


def test_check_reports_file_ahead_and_writes_nothing(tmp_path, monkeypatch, capsys):
    world = World(tmp_path, monkeypatch)
    assert world.run("--check") == 1, "an unbuilt board is file-ahead"
    assert world.writes() == []
    world.run("--apply")
    world.write("alpha", "[ok]")
    capsys.readouterr()
    assert world.run("--check") == 1
    assert world.writes() == []
    assert "file-ahead" in capsys.readouterr().out


def test_check_reports_a_card_done_ahead_of_its_row(tmp_path, monkeypatch, capsys):
    world = World(tmp_path, monkeypatch)
    world.run("--apply")
    row = world.issues.repos["b1tninja/alpha"][0]
    world.client.cards[row.node_id].values["Status"] = "Done"  # dragged by hand
    capsys.readouterr()
    assert world.run("--check") == 1
    out = capsys.readouterr().out
    assert "board-ahead" in out
    assert "b1tninja/alpha/issues/1 is Done on the board" in out
    assert world.writes() == []


def test_check_reports_a_closed_issue_for_an_open_row(tmp_path, monkeypatch, capsys):
    world = World(tmp_path, monkeypatch)
    world.run("--apply")
    world.issues.close("b1tninja/beta", 2)
    capsys.readouterr()
    assert world.run("--check") == 1
    assert "b1tninja/beta/issues/2 is closed" in capsys.readouterr().out


def test_check_reports_a_card_the_files_no_longer_name(tmp_path, monkeypatch, capsys):
    world = World(tmp_path, monkeypatch)
    world.run("--apply")
    world.issues.seed(b.PORTFOLIO_REPO, "gone", b.MARK.format("project", "gone"))
    capsys.readouterr()
    assert world.run("--check") == 1
    assert "<!-- portfolio:project:gone -->" in capsys.readouterr().out


def test_check_fails_when_more_than_two_cards_are_active(tmp_path, monkeypatch, capsys):
    world = World(tmp_path, monkeypatch)
    world.run("--apply")
    cards = list(world.client.cards.values())
    for card in cards[:2]:
        card.values["Active"] = "Active"
    assert world.run("--check") == 0, "two Active cards are within the rule"
    cards[2].values["Active"] = "Active"
    capsys.readouterr()
    assert world.run("--check") == 1
    assert "3 cards are Active" in capsys.readouterr().out


def test_failed_links_are_counted_and_fail_the_run(tmp_path, monkeypatch, capsys):
    world = World(tmp_path, monkeypatch)
    world.client.fail_links = True
    assert world.run("--apply") == 1
    assert "write(s) failed" in capsys.readouterr().err


def test_a_filter_naming_no_project_is_refused(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    assert world.run("--apply", "--project", "gamma") == 1
    assert world.writes() == []


def test_issue_bodies_hold_no_local_paths(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    blurb = f"beta, see {(tmp_path / 'beta' / 'README.md').as_posix()} and D:/elsewhere/notes.md"
    world.states["beta"] = dataclasses.replace(
        world.states["beta"], repo="", blurb=blurb
    )
    world.run("--apply")
    for issues in world.issues.repos.values():
        for issue in issues:
            for text in (issue.title, issue.body):
                assert "D:/" not in text and str(tmp_path) not in text, text
    assert (
        "no GitHub repository yet (`beta/` in the workspace)" in world.card("beta").body
    )
    assert "see beta/README.md and notes.md" in world.card("beta").body


def test_a_create_whose_title_any_board_has_is_refused(tmp_path, monkeypatch, capsys):
    world = World(tmp_path, monkeypatch)
    world.client.projects = [g.Project("OLD", 2, "Protocol SDK portfolio", True)]
    argv = ["--create", "Protocol SDK portfolio"]
    assert b.main(argv, client=world.client, issues=world.issues) == 1
    assert "#2" in capsys.readouterr().err
    assert world.writes() == []


def test_the_board_is_chosen_by_number_and_defaults_to_the_configured_one(
    tmp_path, monkeypatch
):
    world = World(tmp_path, monkeypatch)
    world.run("--apply")
    world.client.writes.clear()
    assert b.main(["--check"], client=world.client, issues=world.issues) == 0
    assert b.BOARD_NUMBER == int(b.os.environ.get("FORGE_BOARD") or 4)
