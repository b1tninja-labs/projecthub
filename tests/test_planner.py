"""The guard is the point. Everything else in planner.py is a convenience read.

These tests do not touch the network, the cloud, or this machine's directory layout. The `workspace`
fixture builds a project tree in tmp and points planner.ROOT at it: an earlier version read the real
D:/code checkout, passed locally, and failed in CI where that path does not exist.
"""

from __future__ import annotations

import pytest

from projecthub.mcp import read as p

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


def _tools():
    pytest.importorskip("mcp")
    import anyio

    app = p.build()
    listed = anyio.run(app.list_tools)
    return app, list(getattr(listed, "tools", listed))


def test_mcp_tools_are_four_read_only_tools_with_output_schemas():
    _, tools = _tools()
    names = sorted(t.name for t in tools)
    assert names == [
        "forge_context",
        "forge_next",
        "forge_packet",
        "forge_propose_status",
        "forge_status",
    ]
    for tool in tools:
        assert (
            tool.annotations is not None and tool.annotations.read_only_hint is True
        ), tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert tool.output_schema, f"{tool.name} declares no outputSchema"


def test_mcp_tools_cannot_write_roadmap(workspace):
    """The MCP-writes decision: no tool reachable over MCP writes ROADMAP.md, even for a change the guard would allow."""
    import anyio

    app, tools = _tools()
    path = workspace / "pkix" / "ROADMAP.md"
    path.write_text(REAL_TEST_ROW, encoding="utf-8")
    for tool in tools:
        params = set(tool.input_schema.get("properties", {}))
        assert "write" not in params, f"{tool.name} takes a write flag"
    preview = anyio.run(
        app.call_tool,
        "forge_propose_status",
        {"project": "pkix", "row": "x.y", "status": "[part]"},
    )
    assert preview.structured_content["would_write"] is True
    assert "--write" in preview.structured_content["how_to_write"]
    assert path.read_text(encoding="utf-8") == REAL_TEST_ROW, (
        "the preview changed the file"
    )


def test_a_refusal_is_an_error_not_a_success_that_reads_refused(workspace):
    import anyio
    from mcp.server.mcpserver.exceptions import ToolError

    app, _ = _tools()
    (workspace / "pkix" / "ROADMAP.md").write_text(PHANTOM_ROW, encoding="utf-8")
    with pytest.raises(ToolError, match="REFUSED"):
        anyio.run(
            app.call_tool,
            "forge_propose_status",
            {"project": "pkix", "row": "a.b", "status": "[ok]"},
        )


def test_the_cli_writes_and_exits_nonzero_on_a_refusal(workspace, capsys):
    path = workspace / "pkix" / "ROADMAP.md"
    path.write_text(PHANTOM_ROW, encoding="utf-8")
    assert p.cli(["status", "pkix", "a.b", "[ok]", "--write"]) == 1
    assert path.read_text(encoding="utf-8") == PHANTOM_ROW
    assert p.cli(["status", "pkix", "a.b", "[part]", "--write"]) == 0
    assert "`[part]`" in path.read_text(encoding="utf-8")


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


def _project(root, name, rows, pyproject=True):
    folder = root / name
    (folder / "tests").mkdir(parents=True)
    if pyproject:
        (folder / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    table = "| Obligation | Spec | Status | Where | Evidence | Milestone |\n|---|---|---|---|---|---|\n"
    (folder / "ROADMAP.md").write_text(
        "- M1 first\n- M2 second\n\n" + table + "".join(rows), encoding="utf-8"
    )
    (folder / "tests" / "test_a.py").write_text(
        "def test_real():\n    assert 1\n", encoding="utf-8"
    )


def test_named_test_comes_from_evidence_when_where_is_a_module(workspace):
    root = workspace
    _project(root, "ledger", [])
    from projecthub.db import parser as roadmap

    where = "`ledger/fanout.py` (planned)"
    assert roadmap.named_test(
        root, "ledger", where, "`tests/test_a.py::test_real`"
    ) == (
        "tests/test_a.py::test_real",
        "exists",
    )
    assert roadmap.named_test(
        root, "ledger", where, "`tests/test_b.py::test_x` (to be written)"
    ) == (
        "tests/test_b.py::test_x",
        "to be written",
    )
    assert roadmap.named_test(root, "ledger", "`test_real`", "")[1] == "exists"
    assert roadmap.named_test(root, "ledger", where, "") == ("", "none")


def test_ready_rows_hold_back_inactive_plan_and_blocked_projects(
    workspace, monkeypatch
):
    from projecthub import registry as portfolio

    root = workspace
    open_row = "| `a.{n}()` | RFC 1 | `[ ]` | `tests/test_a.py::test_{n}` (to be written) | | M{m} |\n"
    done_row = "| `a.done()` | RFC 1 | `[ok]` | `tests/test_a.py::test_real` | | M1 |\n"
    written = "| `a.w()` | RFC 1 | `[ ]` | `tests/test_a.py::test_real` | | M2 |\n"
    _project(
        root,
        "alpha",
        [open_row.format(n="x", m=2), open_row.format(n="y", m=1), written, done_row],
    )
    _project(root, "beta", [open_row.format(n="z", m=1)])
    _project(root, "plan", [open_row.format(n="p", m=1)], pyproject=False)
    _project(root, "gamma", [open_row.format(n="g", m=1)])
    entries = [
        portfolio.Entry("alpha", "Library", 1, 1, "alpha", "", "s4"),
        portfolio.Entry("beta", "Library", 2, 2, "beta", "", "s4", ("alpha",)),
        portfolio.Entry("plan", "Plan", 1, 3, "plan", "", "s4"),
        portfolio.Entry("gamma", "Library", 3, 3, "gamma", "", "s4", ("beta",)),
    ]
    labels = {
        "alpha": "Implemented",
        "beta": "Shell",
        "plan": "Plan only",
        "gamma": "Shell",
    }
    monkeypatch.setattr(portfolio, "entries", lambda: entries)
    monkeypatch.setattr(
        portfolio,
        "state",
        lambda _, e: portfolio.State(labels[e.name], 0, 0, "ROADMAP.md", ""),
    )
    got = p.ready_rows({"alpha", "plan", "gamma"}, root=root)
    assert [r["row_id"] for r in got["ready"]] == ["a.w", "a.y", "a.x"], (
        "written test first, then M1 before M2"
    )
    assert (
        got["ready"][0]["unblocks"] == 1
        and "1 project(s) wait on alpha" in got["ready"][0]["why"]
    )
    assert got["skipped"] == {
        "blocked by beta (not yet code)": 1,
        "plan directory: the deliverable is a document": 1,
        "project not Active": 1,
    }
    assert got["ready_total"] == 3 and got["truncated"] is False
    assert p.ready_rows({"alpha"}, limit=1, root=root)["truncated"] is True


def test_a_leased_row_is_held_back_as_in_progress(workspace, monkeypatch):
    from projecthub import registry as portfolio

    _project(
        workspace,
        "alpha",
        ["| `a.x()` | RFC 1 | `[ ]` | `tests/test_a.py::test_real` | | M1 |\n"],
    )
    monkeypatch.setattr(
        portfolio,
        "entries",
        lambda: [portfolio.Entry("alpha", "Library", 1, 1, "alpha", "", "s4")],
    )
    monkeypatch.setattr(
        portfolio,
        "state",
        lambda _, e: portfolio.State("Implemented", 0, 0, "ROADMAP.md", ""),
    )
    got = p.ready_rows({"alpha"}, root=workspace, leased={"alpha:a.x"})
    assert got["ready"] == [] and got["skipped"] == {"leased: in progress": 1}


def test_a_packet_stays_under_the_limit_and_carries_no_specification_text(
    workspace, monkeypatch
):
    import json

    from projecthub import registry as portfolio

    row = "| `a.x()` | RFC 5280 sec. 6.1 | `[ ]` | `tests/test_a.py::test_x` (to be written) | | M1 |\n"
    _project(workspace, "alpha", [row])
    roadmap_file = workspace / "alpha" / "ROADMAP.md"
    huge = " ".join(
        ["`a.x()` needs the long note."] * 4000
    )  # ~120 KB of prose about the row
    roadmap_file.write_text(
        roadmap_file.read_text(encoding="utf-8") + "\n" + huge + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        portfolio,
        "entries",
        lambda: [portfolio.Entry("alpha", "Library", 1, 1, "alpha", "", "s4")],
    )
    monkeypatch.setattr(
        portfolio,
        "state",
        lambda _, e: portfolio.State("Implemented", 1, 0, "ROADMAP.md", ""),
    )
    got = p.packet("alpha:a.x", root=workspace)
    assert len(json.dumps(got)) <= p.PACKET_LIMIT, (
        "a 120 KB note cannot push a packet past the limit"
    )
    monkeypatch.setattr(p, "PACKET_LIMIT", 3000)
    tight = p.packet("alpha:a.x", root=workspace)
    assert len(json.dumps(tight)) <= 3000 and tight["truncated"] == ["notes"], (
        "cut, and said so"
    )
    assert got["spec"] == "RFC 5280 sec. 6.1", (
        "the spec is the cell, never the document's text"
    )
    assert "rfclib" in got["spec_note"]
    assert (
        got["named_test"] == "tests/test_a.py::test_x"
        and got["test_state"] == "to be written"
    )
    assert got["acceptance"]["test"].endswith("tests/test_a.py::test_x")
    assert got["milestone_text"] == "first"


def test_a_packet_refuses_plan_directories_unknown_and_ambiguous_rows(
    workspace, monkeypatch
):
    from projecthub import registry as portfolio

    row = "| `a.x()` | RFC 1 | `[ ]` | `tests/test_a.py::test_x` (to be written) | | M1 |\n"
    _project(workspace, "alpha", [row])
    _project(workspace, "beta", [row])
    _project(workspace, "plan", [row], pyproject=False)
    entries = [
        portfolio.Entry("alpha", "Library", 1, 1, "alpha", "", "s4"),
        portfolio.Entry("beta", "Library", 1, 1, "beta", "", "s4"),
        portfolio.Entry("plan", "Plan", 1, 1, "plan", "", "s4"),
    ]
    monkeypatch.setattr(portfolio, "entries", lambda: entries)
    monkeypatch.setattr(
        portfolio,
        "state",
        lambda _, e: portfolio.State("Shell", 1, 0, "ROADMAP.md", ""),
    )
    with pytest.raises(LookupError, match="use project:row"):
        p.packet("a.x", root=workspace)
    with pytest.raises(LookupError, match="plan directory"):
        p.packet("plan:a.x", root=workspace)
    with pytest.raises(LookupError, match="no project"):
        p.packet("nope:a.x", root=workspace)
    assert p.packet("beta:a.x", root=workspace)["project"] == "beta"
