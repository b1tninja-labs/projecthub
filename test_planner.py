"""The guard is the point. Everything else in planner.py is a convenience read.

These tests do not touch the network, the cloud, or this machine's directory layout. The `workspace`
fixture builds a project tree in tmp and points planner.ROOT at it: an earlier version read the real
D:/code checkout, passed locally, and failed in CI where that path does not exist.
"""

from __future__ import annotations

import pytest

import planner as p

REAL_TEST_ROW = "| `x.y()` | RFC 5280 | `[ ]` | `tests/test_roadmap_rows.py::test_every_row_names_a_real_callable` |\n"
PHANTOM_ROW = (
    "| `a.b()` | RFC 5280 | `[ ]` | `tests/test_obligations.py::load_anchor` |\n"
)
NONGOAL_ROW = "| `crypto.parse()` | design | `[no]` | `tests/test_roadmap_rows.py::test_matrix_has_rows` |\n"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A project tree the guard can check against, so no assertion depends on this machine's layout."""
    project = tmp_path / "pkix"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_roadmap_rows.py").write_text(
        "def test_matrix_has_rows():\n    assert True\n\n"
        "def test_every_row_names_a_real_callable():\n    assert True\n\n"
        "def test_load_anchor_helper():\n    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(p, "ROOT", tmp_path)
    return tmp_path


def test_open_row_can_move_to_partial():
    updated, refusal = p.rewrite_row(PHANTOM_ROW, "pkix", "a.b", "[part]")
    assert refusal == "", refusal
    assert "`[part]`" in updated


def test_done_is_refused_when_the_named_test_file_is_absent():
    updated, refusal = p.rewrite_row(PHANTOM_ROW, "no-such-project", "a.b", "[ok]")
    assert "REFUSED" not in refusal  # rewrite_row returns the refusal separately
    assert refusal and "does not exist" in refusal
    assert updated == PHANTOM_ROW


def test_done_is_refused_when_the_symbol_is_absent(workspace):
    _, refusal = p.rewrite_row(
        "| `x.y()` | s | `[ ]` | `tests/test_roadmap_rows.py::test_nobody_wrote_this` |\n",
        "pkix",
        "x.y",
        "[ok]",
    )
    assert "defines no test_nobody_wrote_this" in refusal


def test_done_is_permitted_when_the_test_file_defines_the_symbol(workspace):
    updated, refusal = p.rewrite_row(REAL_TEST_ROW, "pkix", "x.y", "[x]")
    assert refusal == "", refusal
    assert "`[x]`" in updated


def test_a_declared_non_goal_is_not_a_status_to_flip():
    _, refusal = p.rewrite_row(NONGOAL_ROW, "pkix", "crypto.parse", "[ok]")
    assert "non-goal" in refusal


def test_unknown_status_and_unknown_row_are_rejected():
    assert "not a status" in p.rewrite_row(PHANTOM_ROW, "pkix", "a.b", "done!")[1]
    assert "no row named" in p.rewrite_row(PHANTOM_ROW, "pkix", "q.r", "[part]")[1]


def test_evidence_reports_the_file_it_checked(workspace):
    ok, why = p.test_evidence(
        "pkix", "`tests/test_roadmap_rows.py::test_matrix_has_rows`"
    )
    assert ok and why == "tests/test_roadmap_rows.py defines test_matrix_has_rows"


def test_propose_status_refuses_and_leaves_the_file_alone(workspace):
    path = workspace / "pkix" / "ROADMAP.md"
    path.write_text(PHANTOM_ROW, encoding="utf-8")
    outcome = p.propose_status("pkix", "a.b", "[ok]", True)
    assert outcome.startswith("REFUSED:")
    assert path.read_text(encoding="utf-8") == PHANTOM_ROW


def test_a_helper_with_a_longer_name_is_not_evidence(workspace):
    row = "| `a.b()` | s | `[ ]` | `tests/test_roadmap_rows.py::test_load_anchor` |\n"
    _, refusal = p.rewrite_row(row, "pkix", "a.b", "[ok]")
    assert "defines no test_load_anchor" in refusal, (
        "def test_load_anchor_helper must not satisfy ::test_load_anchor"
    )


def test_a_planned_test_is_not_evidence(workspace):
    row = "| `a.b()` | s | `[ ]` | `tests/test_roadmap_rows.py::test_matrix_has_rows` (to be written) |\n"
    _, refusal = p.rewrite_row(row, "pkix", "a.b", "[ok]")
    assert "(to be written)" in refusal


def test_only_the_status_cell_changes(workspace):
    text = (
        "| Id | Spec | Status | Where | Evidence |\n|---|---|---|---|---|\n"
        "| `cl.ambient-trust`  |  Model I1,  L1 \\| L2 | [ ] | `anchors.py`   | `test_matrix_has_rows` |\n"
        "| `cl.other` | Model I2 | [ ] | `x.py` | - |\n"
    )
    updated, refusal = p.rewrite_row(text, "pkix", "cl.ambient-trust", "[x]")
    assert refusal == "", refusal
    assert updated == text.replace("| [ ] | `anchors.py`", "| [x] | `anchors.py`", 1)


def test_the_status_column_is_found_by_header(workspace):
    text = (
        "| Id | Status | Spec | Where |\n|---|---|---|---|\n| `x.y` | [ ] | [ ] | w |\n"
    )
    updated, refusal = p.rewrite_row(text, "pkix", "x.y", "[part]")
    assert refusal == "", refusal
    assert updated.endswith("| `x.y` | [part] | [ ] | w |\n")


def test_mcp_surface_exposes_exactly_five_tools():
    pytest.importorskip("mcp")
    import anyio

    listed = anyio.run(p.build().list_tools)
    names = sorted(t.name for t in getattr(listed, "tools", listed))
    assert len(names) == 5, names
    assert all(n.startswith("forge_") for n in names), names


class FakeIssues:
    """The two reads claim() makes, and the edits it issues. `after` is what the re-read returns."""

    def __init__(self, monkeypatch, before, after):
        self.edits: list[tuple[str, str]] = []
        issue = {
            "number": 4,
            "url": "https://x/4",
            "body": "<!-- roadmap:a.b -->",
            "assignees": [{"login": n} for n in before],
        }

        def fake_json(argv):
            if argv[:2] == ["issue", "list"]:
                return [issue]
            return {"assignees": [{"login": n} for n in after]}

        def fake_edit(repo, number, flag, who):
            self.edits.append((flag, who))
            return ""

        monkeypatch.setattr(p, "_gh_json", fake_json)
        monkeypatch.setattr(p, "_gh_edit_assignee", fake_edit)


def test_claim_succeeds_only_when_the_reread_shows_the_caller_alone(monkeypatch):
    issues = FakeIssues(monkeypatch, before=[], after=["me"])
    assert p.claim("pkix", "a.b", "me").startswith("claimed")
    assert issues.edits == [("--add-assignee", "me")]


def test_claim_backs_off_when_a_contender_was_added_at_the_same_time(monkeypatch):
    issues = FakeIssues(monkeypatch, before=[], after=["me", "rival"])
    outcome = p.claim("pkix", "a.b", "me")
    assert outcome.startswith("REFUSED") and "rival" in outcome
    assert issues.edits == [("--add-assignee", "me"), ("--remove-assignee", "me")]


def test_claim_refuses_without_writing_when_someone_already_holds_it(monkeypatch):
    issues = FakeIssues(monkeypatch, before=["rival"], after=["rival"])
    assert p.claim("pkix", "a.b", "me").startswith("REFUSED")
    assert issues.edits == []


def test_claim_by_the_current_holder_is_idempotent(monkeypatch):
    issues = FakeIssues(monkeypatch, before=["me"], after=["me"])
    assert "already yours" in p.claim("pkix", "a.b", "me")
    assert issues.edits == []


def test_claim_is_unconfirmed_when_the_reread_fails(monkeypatch):
    issues = FakeIssues(monkeypatch, before=[], after=[])
    real = p._gh_json
    monkeypatch.setattr(
        p,
        "_gh_json",
        lambda argv: real(argv) if argv[:2] == ["issue", "list"] else {"error": "boom"},
    )
    assert p.claim("pkix", "a.b", "me").startswith("UNCONFIRMED")
    assert issues.edits == [("--add-assignee", "me")]
