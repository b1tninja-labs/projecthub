"""The Rust repositories' obligation tables: `| Status | Obligation | Anchor | Test |`, kept in several files.

berkit, cardkit and piv-card write `[ok]`, `[unread]` and `[open]` in the first column and have no id column.
These tests read excerpts of their real ROADMAPs (tests/fixtures/rust/, copied from the public repositories)
and pin the three things sync depends on: what a status means, which id a row gets and how long that id
holds, and which files a project's rows live in. The Python projects' grammar is pinned by test_roadmap.py
and test_sync_roadmap.py, which this change leaves as they were.
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

from projecthub.db import parser as roadmap
from projecthub.github import sync as s

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "rust"


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    """The excerpts laid out as a workspace: root/berkit, root/cardkit, root/piv-card."""
    target = tmp_path / "ws"
    shutil.copytree(FIXTURES, target)
    return target


def table(*rows: str, header: str = "| Status | Obligation | Anchor | Test |") -> str:
    return "\n".join([header, "|---|---|---|---|", *rows]) + "\n"


# ---------------------------------------------------------------------------------------------- statuses


def test_the_three_statuses_map_onto_the_existing_state_model():
    rows = roadmap.parse(
        table(
            "| [ok] | Done thing | X.690 8.1 | `t::done` |",
            "| [unread] | Half thing | X.690 8.2 | `t::half` |",
            "| [open] | Open thing | none | none |",
        )
    ).rows
    assert [(r["status"], r["state"]) for r in rows] == [
        ("[ok]", "done"),
        ("[unread]", "part"),
        ("[open]", "open"),
    ]
    # Sync closes only a done row. `[unread]` stays open as partial work and `[open]` as unimplemented.
    assert [s.action_of(r) for r in rows] == [
        ("status:done", "close"),
        ("status:partial", "open"),
        ("status:unimplemented", "open"),
    ]


def test_unread_is_never_done_even_with_a_passing_test_named():
    (row,) = roadmap.parse(
        table("| [unread] | Read me | X.690 8.1 | `t::passes` |")
    ).rows
    assert row["state"] != "done"
    assert s.action_of(row)[1] == "open"


def test_the_row_columns_are_found_by_header_name():
    (row,) = roadmap.parse(
        "| Status | Anchor | Test | Obligation |\n|---|---|---|---|\n"
        "| [ok] | X.690 8.1 | `t::a` | The obligation |\n"
    ).rows
    assert (row["obligation"], row["spec"], row["where"]) == (
        "The obligation",
        "X.690 8.1",
        "`t::a`",
    )


def test_a_done_row_must_name_a_test_or_it_is_reported_not_counted():
    parsed = roadmap.parse(
        table(
            "| [ok] | Review only | design | none: reviewed the source |",
            "| [ok] | Empty test cell | design |  |",
            "| [ok] | Has one | design | `t::real` |",
        )
    )
    assert [r["obligation"] for r in parsed.rows] == ["Has one"]
    assert len(parsed.problems) == 2
    assert all("names no test" in p.reason for p in parsed.problems)


def test_a_table_with_no_test_column_holds_only_unfinished_rows():
    parsed = roadmap.parse(
        "| Status | Obligation | Anchor |\n|---|---|---|\n"
        "| [ok] | Claims done | X.690 |\n"
        "| [open] | Not done | X.690 |\n"
        "| [unread] | Unread | X.690 |\n"
    )
    assert [r["status"] for r in parsed.rows] == ["[open]", "[unread]"]
    assert [r["where"] for r in parsed.rows] == ["", ""]
    assert len(parsed.problems) == 1 and "[ok]" in parsed.problems[0].reason


def test_a_status_the_grammar_does_not_know_is_reported():
    parsed = roadmap.parse(table("| [maybe] | Odd | a | b |"))
    assert parsed.rows == []
    assert "not a recognised status" in parsed.problems[0].reason


def test_policy_module_and_assumption_tables_are_not_obligation_tables(root):
    """berkit's policy table (Status | Policy | Why), the module summary and cardkit's assumption register
    (Status | Ambiguity | ...) carry statuses but are not obligations; only `Obligation` headers are rows."""
    berkit, _ = roadmap.read_matrices(root, "berkit")
    texts = [r["obligation"] for r in berkit.rows]
    assert not any(t.startswith("BER decoding enforces") for t in texts)
    cardkit, _ = roadmap.read_matrices(root, "cardkit")
    assert not any(r["obligation"].startswith("A1: IAD source") for r in cardkit.rows)


# ---------------------------------------------------------------------------------------------- ids


def test_ids_are_derived_valid_and_the_same_on_every_read(root):
    first, _ = roadmap.read_matrices(root, "berkit")
    second, _ = roadmap.read_matrices(root, "berkit")
    names = [r["name"] for r in first.rows]
    assert names == [r["name"] for r in second.rows]
    assert len(names) == len(set(names)) == 17
    for name in names:
        assert roadmap.ID_RE.match(name), name


def test_an_id_is_scoped_by_the_file_it_comes_from(root):
    rows, _ = roadmap.read_matrices(root, "berkit")
    scopes = {r["file"]: r["name"].split(".")[0] for r in rows.rows}
    assert scopes == {
        "ROADMAP.md": "roadmap",
        "crates/berkit/roadmap/corpus.md": "corpus",
        "crates/berkit/roadmap/oid.md": "oid",
    }
    rows, _ = roadmap.read_matrices(root, "cardkit")
    # A hyphen is not allowed in an id prefix, so crates/apdu-core/ROADMAP.md is `apdu_core`.
    assert {r["name"].split(".")[0] for r in rows.rows} == {
        "roadmap",
        "apdu_core",
        "ccid_proto",
    }


def test_ids_survive_reordering_and_inserting_rows():
    a = "| [open] | First obligation of the table | x | none |"
    b = "| [ok] | Second obligation of the table | x | `t::b` |"
    c = "| [open] | Third obligation of the table | x | none |"
    before = {r["obligation"]: r["name"] for r in roadmap.parse(table(a, b, c)).rows}
    new = "| [open] | Brand new obligation | x | none |"
    after = {
        r["obligation"]: r["name"] for r in roadmap.parse(table(c, new, a, b)).rows
    }
    for text in before:
        assert after[text] == before[text], (
            "reordering or inserting must not rename a row"
        )


def test_an_edit_past_the_first_80_characters_or_to_status_or_test_keeps_the_id():
    head = "A long obligation whose opening words stay put while the tail is edited, 80 chars"
    old = f"| [open] | {head} and 22 of 26 rows | a | none |"
    new = f"| [ok] | {head} and 26 of 26 rows | a changed | `t::now` |"
    (a,) = roadmap.parse(table(old)).rows
    (b,) = roadmap.parse(table(new)).rows
    assert a["name"] == b["name"]


def test_an_edit_to_the_first_words_renames_the_row_which_is_the_documented_limit():
    (a,) = roadmap.parse(table("| [open] | The reader refuses X | a | none |")).rows
    (b,) = roadmap.parse(table("| [open] | The decoder refuses X | a | none |")).rows
    assert a["name"] != b["name"]


def test_two_rows_that_agree_on_their_first_80_characters_are_told_apart_in_order():
    same = "| [open] | Identical opening text, repeated in a second row of one file | a | none |"
    one, two = roadmap.parse(table(same, same)).rows
    assert one["name"] != two["name"]
    assert two["name"] == one["name"] + "-2"


def test_the_same_text_in_two_files_does_not_collide(root):
    folder = root / "berkit" / "crates" / "berkit" / "roadmap"
    row = "| [open] | Shared sentence in two files | x | none |"
    (folder / "one.md").write_text(table(row), encoding="utf-8")
    (folder / "two.md").write_text(table(row), encoding="utf-8")
    rows, _ = roadmap.read_matrices(root, "berkit")
    shared = [r["name"] for r in rows.rows if r["obligation"].startswith("Shared")]
    assert sorted(shared) == sorted(set(shared)) and len(shared) == 2


def test_two_files_deriving_one_scope_are_refused(root):
    # crates/other/oid.md derives the scope `oid` too, as crates/berkit/roadmap/oid.md does.
    other = root / "berkit" / "crates" / "other" / "oid.md"
    other.parent.mkdir(parents=True)
    other.write_text(table("| [open] | Elsewhere | x | none |"), encoding="utf-8")
    with pytest.raises(ValueError, match="scope"):
        roadmap.read_matrices(
            root,
            "berkit",
            [
                root / "berkit" / "crates" / "berkit" / "roadmap" / "oid.md",
                other,
            ],
        )


# ---------------------------------------------------------------------------------------------- files


def test_berkit_rows_come_from_the_root_file_and_every_fragment(root):
    files = roadmap.matrix_files(root, "berkit")
    assert [f.relative_to(root / "berkit").as_posix() for f in files] == [
        "ROADMAP.md",
        "crates/berkit/roadmap/corpus.md",
        "crates/berkit/roadmap/oid.md",
    ]


def test_cardkit_rows_come_from_the_root_file_and_each_crates_roadmap(root):
    files = roadmap.matrix_files(root, "cardkit")
    assert [f.relative_to(root / "cardkit").as_posix() for f in files] == [
        "ROADMAP.md",
        "crates/apdu-core/ROADMAP.md",
        "crates/ccid-proto/ROADMAP.md",
    ]


def test_piv_card_is_one_file_and_not_the_private_one(root):
    private = root / "piv-card" / ".private"
    private.mkdir()
    (private / "ROADMAP.md").write_text(table("| [open] | Secret | x | none |"))
    files = roadmap.matrix_files(root, "piv-card")
    assert [f.name for f in files] == ["ROADMAP.md"]
    assert files[0].parent.name == "piv-card"


def test_a_fragment_with_no_rows_is_not_listed(root):
    (root / "berkit" / "crates" / "berkit" / "roadmap" / "notes.md").write_text(
        "# notes\n\nno table here\n", encoding="utf-8"
    )
    names = [f.name for f in roadmap.matrix_files(root, "berkit")]
    assert "notes.md" not in names


def test_a_python_project_still_has_exactly_its_one_matrix_file(tmp_path):
    folder = tmp_path / "pkix"
    folder.mkdir()
    (folder / "ROADMAP.md").write_text(
        "| Obligation | Spec | Status | Where |\n|---|---|---|---|\n"
        "| `a.one()` | RFC | `[ ]` | `tests/t.py::test_one` |\n",
        encoding="utf-8",
    )
    assert roadmap.matrix_files(tmp_path, "pkix") == [folder / "ROADMAP.md"]
    (row,) = roadmap.read_matrices(tmp_path, "pkix")[0].rows
    assert row["name"] == "a.one" and "file" not in row and "obligation" not in row


def test_a_problem_names_its_file(root):
    parsed, _ = roadmap.read_matrices(root, "cardkit")
    assert [(p.file, "names no test" in p.reason) for p in parsed.problems] == [
        ("crates/apdu-core/ROADMAP.md", True)
    ]


# ---------------------------------------------------------------------------------------------- sync


class Done:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


class FakeGh:
    def __init__(self, issues=()):
        self.issues = list(issues)
        self.writes: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        args = list(argv[1:])
        if args[:2] == ["repo", "view"]:
            return Done('{"name": "r"}')
        if args[:2] == ["issue", "list"]:
            return Done(json.dumps(self.issues))
        if args[:2] == ["label", "list"]:
            return Done("[]")
        self.writes.append(args)
        return Done()

    def creates(self) -> list[list[str]]:
        return [w for w in self.writes if w[:2] == ["issue", "create"]]


@pytest.fixture
def fake(monkeypatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(s.subprocess, "run", fake)
    monkeypatch.setattr(s, "gh", lambda: "gh")
    monkeypatch.setattr(s, "PACER", s.Pacer(delay=0, sleep=lambda _: None))
    return fake


def marked(
    project: str, name: str, number: int, state: str = "OPEN"
) -> dict[str, object]:
    return {
        "number": number,
        "title": f"[{project}] t",
        "body": f"{s.MARKER.format(name)}\n",
        "state": state,
        "milestone": None,
    }


def test_sync_files_one_issue_per_unfinished_row_across_every_file(root, fake):
    assert s.sync_one("berkit", "b1tninja-labs/berkit", True, False, root) == 0
    created = fake.creates()
    # Unfinished rows: 1 `[unread]` and 1 `[open]` in the root file; the fragments hold only `[ok]` here.
    assert len(created) == 2
    labels = sorted(w[w.index("--label") + 1] for w in created)
    assert labels == ["status:partial", "status:unimplemented"]
    bodies = [pathlib.Path(w[w.index("--body-file") + 1]) for w in created]
    assert all(not b.exists() for b in bodies), "the temporary body files are removed"


def test_sync_is_idempotent_a_second_run_with_the_issues_present_creates_none(
    root, fake
):
    rows, _ = roadmap.read_matrices(root, "cardkit")
    open_rows = [r for r in rows.rows if s.action_of(r)[1] == "open"]
    assert len(open_rows) == 2
    fake.issues = [marked("cardkit", r["name"], n) for n, r in enumerate(open_rows, 1)]
    assert s.sync_one("cardkit", "o/cardkit", True, False, root) == 0
    assert fake.creates() == []


def test_check_reports_drift_in_a_fragment_row_and_a_done_row_with_no_test(
    root, fake, capsys
):
    assert s.sync_one("cardkit", "o/cardkit", False, True, root) == 3
    err = capsys.readouterr().err
    assert err.count("DRIFT:") == 3
    assert "crates/apdu-core/ROADMAP.md:7 looks like a row but does not parse" in err


def test_a_renamed_row_shows_as_two_drifts_never_as_a_silently_moved_issue(
    root, fake, capsys
):
    fake.issues = [marked("piv-card", "roadmap.an-id-nobody-derives-000000", 9)]
    s.sync_one("piv-card", "o/piv-card", False, True, root)
    err = capsys.readouterr().err
    assert "marks roadmap.an-id-nobody-derives-000000, which is no longer a row" in err


def test_the_issue_for_a_rust_row_points_at_its_own_file_and_is_not_python_shaped(root):
    rows, text = roadmap.read_matrices(root, "cardkit")
    row = next(r for r in rows.rows if r["status"] == "[unread]")
    summary, detail = s.detail_of(
        "cardkit", "o/cardkit", row, rows.rows, text, root, {}
    )
    body = s.body(row, "", detail)
    assert "blob/HEAD/crates/ccid-proto/ROADMAP.md" in body
    assert "`[unread]` to `[ok]`" in body
    assert "cargo clippy" in body and "ruff" not in body and "mypy" not in body
    assert "does not exist yet" not in body, "projecthub cannot check a Rust test"
    assert "Not checked by projecthub" in body
    assert row["where"] in body
    assert s.MARKER.format(row["name"]) in body


def test_a_rust_issue_title_is_short_enough_for_github(root):
    rows, _ = roadmap.read_matrices(root, "berkit")
    for row in rows.rows:
        title = s.issue_title("berkit", row, "")
        assert len(title) <= 150 and title.startswith("[berkit] ")


def test_the_default_run_includes_the_rust_repositories():
    assert {"berkit", "cardkit", "piv-card", "obd-bridge"} <= set(s.default_projects())
