"""The issue text is assembled from the project's own files. These trees are built in tmp.

What they pin is what the first issues lacked: the repository, the purpose, the milestone, and an honest
statement of whether the named test exists.
"""

from __future__ import annotations

import pathlib

import pytest

import rowdoc
import sync_roadmap as s

ROADMAP = """# Roadmap

| Obligation | Spec | Status | Where | Milestone |
|---|---|---|---|---|
| `anchors.load()` | RFC 5280 sec. 6.1 | `[ ]` | `tests/test_a.py::test_load` | M1 |
| `names.check()` | RFC 5280 | `[ ]` | `tests/test_a.py::test_missing` | M2 |
| `store.keep()` | design | `[ ]` | `tests/test_a.py::test_load` | - |

`anchors.load()` is the first slice, and `names.check()` waits on it.

## 2. Milestones

- M1 anchors and chain discovery with a fixture set: real chains
- M2 name constraints, including excluded subtrees
  and the issuance-time exception

## 3. Errata watch

- RFC 5280 carries 6 verified errata.
- RFC 9999 carries 1 verified erratum.
"""

STUB = '''"""trust anchor selection, including name restrictions (RFC 5280 sec. 6.1).

More prose here.
"""


def load(cert_der: bytes, restriction: str | None) -> object:
    """Pending."""
    raise NotImplementedError("load() is not implemented")


def done(x: int) -> int:
    return x
'''


@pytest.fixture
def tree(tmp_path: pathlib.Path) -> pathlib.Path:
    project = tmp_path / "demo-kit"
    (project / "demo_kit").mkdir(parents=True)
    (project / "demo_kit" / "anchors.py").write_text(STUB, encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_a.py").write_text(
        "def test_load():\n    pass\n", encoding="utf-8"
    )
    (project / "ROADMAP.md").write_text(ROADMAP, encoding="utf-8")
    (project / "AGENTS.md").write_text(
        "# Instructions for coding agents\n\nA demo library for anchors.\n\n## Commands\n",
        encoding="utf-8",
    )
    return tmp_path


def test_a_milestone_column_is_read_by_header_and_is_not_mistaken_for_evidence():
    rows = s.rows_of(ROADMAP)
    assert [(r["name"], r["milestone"], r["evidence"]) for r in rows] == [
        ("anchors.load", "M1", ""),
        ("names.check", "M2", ""),
        ("store.keep", "", ""),
    ]


def test_an_evidence_column_is_still_found_by_header():
    table = (
        "| Id | Spec | Status | Where | Evidence |\n|---|---|---|---|---|\n"
        "| `cl.ambient-trust` | Model I1 | `[x]` | `a.py` | `test_x` |\n"
    )
    assert s.rows_of(table)[0]["evidence"] == "`test_x`"


def test_the_ladder_keeps_continuation_lines_and_titles_name_themselves():
    ladder = rowdoc.ladder_of(ROADMAP)
    assert ladder["M2"].endswith("and the issuance-time exception")
    assert (
        rowdoc.milestone_title("M1", ladder)
        == "M1 - anchors and chain discovery with a fixture set"
    )
    assert rowdoc.milestone_title("", ladder) == ""
    assert rowdoc.milestone_title("M9", ladder) == "M9"


def test_the_stub_is_read_without_importing_it(tree):
    stub = rowdoc.stub_of(tree, "demo-kit", "anchors.load")
    assert stub is not None and stub.still_stub
    assert stub.signature == "load(cert_der: bytes, restriction: str | None) -> object"
    assert stub.path == "demo_kit/anchors.py"
    implemented = rowdoc.stub_of(tree, "demo-kit", "anchors.done")
    assert implemented is not None and not implemented.still_stub
    assert rowdoc.stub_of(tree, "demo-kit", "anchors.absent") is None


def test_the_title_summary_ends_at_a_clause_not_mid_phrase(tree):
    stub = rowdoc.stub_of(tree, "demo-kit", "anchors.load")
    assert (
        rowdoc.short_summary(stub)
        == "trust anchor selection, including name restrictions"
    )


def test_the_named_test_is_reported_honestly(tree):
    assert rowdoc.test_evidence(tree, "demo-kit", "`tests/test_a.py::test_load`")[0]
    ok, why = rowdoc.test_evidence(tree, "demo-kit", "`tests/test_a.py::test_missing`")
    assert not ok and "defines no test_missing" in why
    ok, why = rowdoc.test_evidence(tree, "demo-kit", "`tests/test_zzz.py::test_x`")
    assert not ok and "does not exist" in why


def test_the_issue_says_which_repo_what_is_needed_and_what_does_not_exist(tree):
    text = (tree / "demo-kit" / "ROADMAP.md").read_text(encoding="utf-8")
    rows, ladder = s.rows_of(text), rowdoc.ladder_of(text)
    row = next(r for r in rows if r["name"] == "names.check")
    summary, detail = s.detail_of(
        "demo-kit", "me/demo-kit", row, rows, text, tree, ladder
    )
    issue = s.body(row, row["milestone"], detail)
    assert "[`me/demo-kit`](https://github.com/me/demo-kit)" in issue
    assert "M2 - name constraints, including excluded subtrees" in issue
    assert "A demo library for anchors." in issue
    assert "**does not exist yet**" in issue and "defines no test_missing" in issue
    assert "- RFC 5280 carries 6 verified errata." in issue
    assert "RFC 9999" not in issue, (
        "errata for a document the row does not cite must not leak in"
    )
    assert "`names.check()` waits on it" in issue
    assert "<!-- roadmap:names.check -->" in issue


def test_the_title_carries_the_repository(tree):
    row = s.rows_of(ROADMAP)[0]
    assert s.issue_title("demo-kit", row, "trust anchor selection") == (
        "[demo-kit] anchors.load(): trust anchor selection"
    )


def test_an_unplaced_row_says_so_instead_of_inventing_a_milestone(tree):
    text = (tree / "demo-kit" / "ROADMAP.md").read_text(encoding="utf-8")
    rows, ladder = s.rows_of(text), rowdoc.ladder_of(text)
    row = next(r for r in rows if r["name"] == "store.keep")
    _, detail = s.detail_of("demo-kit", "me/demo-kit", row, rows, text, tree, ladder)
    assert "not placed on the ladder in ROADMAP.md" in detail


def test_siblings_exclude_the_row_itself():
    rows = s.rows_of(ROADMAP)
    assert rowdoc.siblings(rows, "M1", "anchors.load") == []
    assert rowdoc.siblings(rows, "", "x") == []


def test_update_issue_edits_only_when_something_differs(tree, monkeypatch):
    text = (tree / "demo-kit" / "ROADMAP.md").read_text(encoding="utf-8")
    rows, ladder = s.rows_of(text), rowdoc.ladder_of(text)
    row = rows[0]
    summary, detail = s.detail_of(
        "demo-kit", "me/demo-kit", row, rows, text, tree, ladder
    )
    current = {
        "number": 3,
        "title": s.issue_title("demo-kit", row, summary),
        "body": s.body(row, row["milestone"], detail).replace("\n", "\r\n"),
        "milestone": {"title": rowdoc.milestone_title("M1", ladder)},
    }
    ran: list[list[str]] = []
    monkeypatch.setattr(s, "run", lambda argv, apply: ran.append(argv) or "")
    s.update_issue(
        "demo-kit", "me/demo-kit", row, rows, text, tree, ladder, current, True
    )
    assert ran == [], "an identical issue (CRLF aside) must not be rewritten"
    stale = {**current, "title": "old", "milestone": None}
    s.update_issue(
        "demo-kit", "me/demo-kit", row, rows, text, tree, ladder, stale, True
    )
    assert len(ran) == 1 and "--milestone" in ran[0] and "--title" in ran[0]
