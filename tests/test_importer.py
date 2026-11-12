"""The importer: every source line placed exactly once, sections typed, citations kept as refs, nothing guessed."""

from __future__ import annotations

import pathlib

import pytest

from projecthub.db import importer as I
from projecthub.db import store as matrix_store

ROADMAP = """# demo roadmap

**The honesty rule:** a row may read `[ok]` only when a named test would fail
if the behaviour regressed.

## 1. Obligations

| Obligation | Spec | Status | Where | Milestone |
|---|---|---|---|---|
| `core.parse` | RFC 8210 section 5.1 | `[ok]` | `tests/test_core.py::test_parse` | M1 |
| `core.verify` | X.690 §8.1.2 | `[ ]` | `tests/test_core.py::test_verify` (to be written) | M2 |

## 2. Milestones

- M1 parse every PDU
- M2 verify the encoding
  with a continuation line

## 3. Errata watch

- RFC 8210 erratum 6024, editorial

## 4. Deliberate non-goals

| Non-goal | Status | Revisit when |
|---|---|---|
| routing policy, which is pathattr | `[no]` | a named document requires it |
| MRT files | `[no]` | a named document requires it |

## 5. Open questions

1. Which RFC 6810 behaviours does v0 still need?
2. Is a cache allowed to send an empty reset?

## 6. Publication checklist

- [ ] PyPI name reserved
- [ ] docs built

## Board projection

The board shows 2 items, a dated snapshot.

## Why this project exists

It exists because nobody else ships RFC 8210 with tests.
"""


@pytest.fixture
def imported(tmp_path: pathlib.Path):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "ROADMAP.md").write_text(ROADMAP, encoding="utf-8")
    importer = I.Importer(matrix_store.Store())
    report = importer.import_project(tmp_path, "demo")
    return importer, report, tmp_path


def _entries(importer, kind=None):
    rows = importer.db.execute(
        "SELECT id, type, title, state FROM entries ORDER BY id"
    ).fetchall()
    return [r for r in rows if kind is None or r[1] == kind]


def test_every_source_line_is_placed_exactly_once(imported):
    importer, report, _ = imported
    assert report["every_line_once"] is True
    assert report["lines"] == len(ROADMAP.splitlines())
    assert (
        report["lines"] == report["placed"] + report["structural"] + report["flagged"]
    )


def test_sections_become_typed_entries(imported):
    importer, report, _ = imported
    assert report["entries"] == {
        "decision": 2,
        "epic": 2,
        "finding": 1,
        "obligation": 2,
        "project": 1,
        "question": 2,
        "rule": 1,
        "task": 2,
    }
    obligations = {r[0]: r[3] for r in _entries(importer, "obligation")}
    assert obligations == {"demo:core.parse": "done", "demo:core.verify": "open"}, (
        "the row id is the entry id"
    )
    decisions = [r[2] for r in _entries(importer, "decision")]
    assert decisions == ["routing policy, which is pathattr", "MRT files"], (
        "each non-goal row is one decision"
    )
    assert {r[3] for r in _entries(importer, "decision")} == {"accepted"}
    (rule,) = _entries(importer, "rule")
    body = importer.db.execute(
        "SELECT body FROM entries WHERE id = ?", (rule[0],)
    ).fetchone()[0]
    assert "if the behaviour regressed" in body, "the rule keeps its continuation line"


def test_obligations_hang_from_their_milestone_epic(imported):
    importer, _, _ = imported
    parents = dict(
        importer.db.execute(
            "SELECT src, dst FROM links WHERE kind = 'parent'"
        ).fetchall()
    )
    assert parents["demo:core.parse"] == "demo:M1"
    assert parents["demo:core.verify"] == "demo:M2"
    assert parents["demo:M1"] == "demo:project"
    m2 = importer.db.execute(
        "SELECT body FROM entries WHERE id = 'demo:M2'"
    ).fetchone()[0]
    assert "with a continuation line" in m2


def test_superseded_and_unclassified_prose_is_flagged_never_dropped(imported):
    importer, report, _ = imported
    reasons = {f["reason"] for f in report["flagged_lines"]}
    assert reasons == {"board projection text: superseded by the GitHub sync"}
    project = importer.db.execute(
        "SELECT body, flagged FROM entries WHERE id = 'demo:project'"
    ).fetchone()
    assert "nobody else ships RFC 8210" in project[0] and project[1], (
        "description prose kept and flagged for review"
    )


def test_citations_are_refs_with_roles_and_start_unresolved(imported):
    importer, _, _ = imported
    cites = set(
        importer.db.execute(
            "SELECT entry, source, ref, role, status FROM citations"
        ).fetchall()
    )
    assert ("demo:core.parse", "rfc", "RFC8210§5.1", "normative", "unresolved") in cites
    assert (
        "demo:core.verify",
        "itu",
        "X.690§8.1.2",
        "normative",
        "unresolved",
    ) in cites
    assert any(c[2] == "RFC6810" and c[3] == "informative" for c in cites), (
        "a question's RFC is informative"
    )
    columns = {r[1] for r in importer.db.execute("PRAGMA table_info(citations)")}
    assert not columns & {"text", "quote", "clause_text", "body"}, (
        "no column can hold clause text"
    )


def test_reimport_is_idempotent_and_keeps_ids(imported):
    importer, first, root = imported
    ids = [r[0] for r in _entries(importer)]
    again = importer.import_project(root, "demo")
    assert [r[0] for r in _entries(importer)] == ids
    assert {k: again[k] for k in ("lines", "placed", "flagged")} == {
        k: first[k] for k in ("lines", "placed", "flagged")
    }


def test_rows_without_ids_get_minted_ids_and_are_flagged(tmp_path):
    (tmp_path / "demo").mkdir()
    text = "# t\n\n| Item | Status |\n|---|---|\n| plain item | [ ] |\n| other item | [ok] |\n"
    (tmp_path / "demo" / "ROADMAP.md").write_text(
        text.replace("Item", "Obligation"), encoding="utf-8"
    )
    importer = I.Importer(matrix_store.Store())
    report = importer.import_project(tmp_path, "demo")
    assert report["minted_ids"] == 2 and report["every_line_once"]
    assert all(
        "minted" in f["why"]
        for f in report["flagged_entries"]
        if f["type"] == "obligation"
    )


def test_the_report_renders_for_review(imported):
    _, report, _ = imported
    text = I.render(report)
    assert "Every line accounted for exactly once: **yes**" in text
    assert "board projection" in text


WORKSPACE = matrix_store.ROOT / "PORTFOLIO.md"


@pytest.mark.skipif(not WORKSPACE.is_file(), reason="needs the D:\\code workspace")
def test_every_real_matrix_imports_with_every_line_placed_once():
    importer = I.Importer(matrix_store.Store())
    bad = []
    for project, _ in matrix_store.matrices(matrix_store.ROOT):
        report = importer.import_project(matrix_store.ROOT, project)
        if not report["every_line_once"]:
            bad.append(project)
    assert bad == []
