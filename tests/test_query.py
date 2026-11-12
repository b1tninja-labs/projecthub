"""projecthub's query, graph and plan reads: every filter, truncation, cycles, Mermaid, and that nothing writes.

The workspace is built in tmp with two projects, beta depending on alpha, and the registry is patched to
name exactly those, so no assertion depends on this machine's layout or on the real portfolio.
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess

import pytest

from projecthub import registry
from projecthub.mcp import query as q
from projecthub.mcp import work

TABLE = "| Obligation | Spec | Status | Where | Evidence | Milestone |\n|---|---|---|---|---|---|\n"
ALPHA_ROWS = [
    "| `a.load()` | RFC 5280 s4.1 | `[ ]` | `tests/test_a.py::test_real` | | M1 |\n",
    "| `a.parse()` | RFC 5280 s4.2 | `[part]` | `tests/test_a.py::test_parse` (to be written) | | M2 |\n",
    "| `a.done()` | RFC 6962 | `[ok]` | `tests/test_a.py::test_real` | | M1 |\n",
    "| `a.never()` | design | `[no]` | `tests/test_a.py::test_gone` | | M2 |\n",
]
BETA_ROWS = [
    "| `b.anchor()` | RFC 9162 | `[ ]` | `tests/test_a.py::test_real` | | M1 |\n",
    "| `b.store()` | RFC 9162 | `[ ]` | `tests/test_a.py::test_store` (to be written) | | M1 |\n",
]


def _project(root: pathlib.Path, name: str, rows: list[str]) -> None:
    folder = root / name
    (folder / "tests").mkdir(parents=True)
    (folder / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (folder / "ROADMAP.md").write_text(
        "- M1 first\n- M2 second\n\n" + TABLE + "".join(rows), encoding="utf-8"
    )
    (folder / "tests" / "test_a.py").write_text(
        "def test_real():\n    assert 1\n", encoding="utf-8"
    )


ENTRIES = [
    registry.Entry("alpha", "Library", 1, 1, "alpha", "", "s4"),
    registry.Entry("beta", "Service", 2, 2, "beta", "", "s4", ("alpha",)),
]
LABELS = {"alpha": "Implemented", "beta": "Shell"}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    _project(root, "alpha", ALPHA_ROWS)
    _project(root, "beta", BETA_ROWS)
    (root / "PORTFOLIO.md").write_text(
        "## 9. Decisions\n\n1. **Should `beta` keep its own store?** Open.\n\n"
        "2. **Is `alpha` frozen?** **Decided** yes.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(registry, "entries", lambda: list(ENTRIES))
    monkeypatch.setattr(
        registry,
        "state",
        lambda _, e: registry.State(LABELS.get(e.name, "Absent"), 0, 0, "", ""),
    )
    monkeypatch.setenv("PROJECTHUB_ROOT", str(root))
    monkeypatch.setenv("PROJECTHUB_LEASES_DB", str(tmp_path / "state" / "leases.db"))
    return root


@pytest.fixture
def leases(workspace, tmp_path):
    """A real lease store: alpha's a.load() is claimed, beta's b.store() ended blocked."""
    store = work.Store(tmp_path / "state" / "leases.db")
    store.acquire("alpha", "a.load", holder="agent-1")
    blocked = store.acquire("beta", "b.store", holder="agent-2")
    store.block(blocked.lease_id, "spec_question", "which RFC 9162 section governs")
    return store


def _ids(out: q.QueryOut) -> list[str]:
    return [f"{r['project']}:{r['row_id']}" for r in out["rows"]]


EMPTY = q.Leases()


# -- query ------------------------------------------------------------------------------------------


def test_no_filter_returns_every_row_sorted_by_wave_tier_project_and_matrix_order(
    workspace,
):
    out = q.query(workspace, {}, leases=EMPTY)
    assert _ids(out) == [
        "alpha:a.load",
        "alpha:a.parse",
        "alpha:a.done",
        "alpha:a.never",
        "beta:b.anchor",
        "beta:b.store",
    ]
    assert out["total"] == 6 and out["truncated"] is False
    first = out["rows"][0]
    assert first["spec"] == "RFC 5280 s4.1" and first["milestone"] == "M1"
    assert first["named_test"] == "tests/test_a.py::test_real"
    assert first["test_state"] == "exists" and first["claim"] is None


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"project": "beta"}, ["beta:b.anchor", "beta:b.store"]),
        ({"state": "open"}, ["alpha:a.load", "beta:b.anchor", "beta:b.store"]),
        ({"state": "part"}, ["alpha:a.parse"]),
        ({"state": "done"}, ["alpha:a.done"]),
        ({"state": "nongoal"}, ["alpha:a.never"]),
        ({"milestone": "m2"}, ["alpha:a.parse", "alpha:a.never"]),
        ({"wave": 2}, ["beta:b.anchor", "beta:b.store"]),
        (
            {"tier": 1},
            ["alpha:a.load", "alpha:a.parse", "alpha:a.done", "alpha:a.never"],
        ),
        ({"kind": "service"}, ["beta:b.anchor", "beta:b.store"]),
        (
            {"has_test": "exists"},
            ["alpha:a.load", "alpha:a.done", "beta:b.anchor"],
        ),
        ({"has_test": "to be written"}, ["alpha:a.parse", "beta:b.store"]),
        ({"has_test": "to_be_written", "project": "beta"}, ["beta:b.store"]),
        ({"has_test": "missing"}, ["alpha:a.never"]),
        ({"spec_contains": "rfc 9162"}, ["beta:b.anchor", "beta:b.store"]),
        ({"text": "ANCHOR"}, ["beta:b.anchor"]),
        ({"text": "parse", "state": "open"}, []),
        ({"project": "", "state": None}, None),  # empty values are no filter
    ],
)
def test_each_filter(workspace, filters, expected):
    out = q.query(workspace, filters, leases=EMPTY)
    if expected is None:
        assert out["total"] == 6
    else:
        assert _ids(out) == expected


def test_claimed_and_blocked_come_from_the_lease_store(workspace, leases):
    out = q.query(workspace, {"claimed": True})
    assert _ids(out) == ["alpha:a.load"]
    assert out["rows"][0]["claim"]["holder"] == "agent-1"
    assert len(_ids(q.query(workspace, {"claimed": False}))) == 5
    blocked = q.query(workspace, {"blocked": "true"})
    assert _ids(blocked) == ["beta:b.store"]
    assert blocked["rows"][0]["block"].startswith("spec_question: which RFC 9162")
    assert "beta:b.store" not in _ids(q.query(workspace, {"blocked": False}))


def test_the_read_only_lease_reader_agrees_with_the_store(workspace, leases):
    seen = q.read_leases()
    assert set(seen.live) == leases.live_keys()
    assert set(seen.blocked) == leases.blocked_keys()


def test_no_lease_database_means_no_leases_and_creates_none(tmp_path):
    missing = tmp_path / "nowhere" / "leases.db"
    assert q.read_leases(missing) == q.Leases()
    assert not missing.parent.exists()


def test_a_limit_truncates_and_says_so(workspace):
    out = q.query(workspace, {}, limit=2, leases=EMPTY)
    assert _ids(out) == ["alpha:a.load", "alpha:a.parse"]
    assert out["total"] == 6 and out["truncated"] is True
    assert q.query(workspace, {}, limit=6, leases=EMPTY)["truncated"] is False


@pytest.mark.parametrize(
    ("filters", "message"),
    [
        ({"colour": "red"}, "unknown filter colour"),
        ({"state": "closed"}, "state must be one of"),
        ({"has_test": "maybe"}, "has_test must be one of"),
        ({"claimed": "perhaps"}, "claimed must be true or false"),
        ({"wave": "two"}, "wave must be a number"),
        ({"project": "gamma"}, "no project 'gamma'"),
    ],
)
def test_bad_filters_are_refused(workspace, filters, message):
    with pytest.raises(q.QueryError, match=message):
        q.query(workspace, filters, leases=EMPTY)


# -- graph ------------------------------------------------------------------------------------------


def test_graph_edges_order_and_open_rows(workspace):
    out = q.graph(workspace)
    assert out["edges"] == [{"project": "beta", "depends_on": "alpha"}]
    assert out["order"] == ["alpha", "beta"] and out["cycles"] == []
    counts = {n["project"]: n["open_rows"] for n in out["nodes"]}
    assert counts == {"alpha": 2, "beta": 2}, (
        "open and part count; done and nongoal do not"
    )
    assert out["mermaid"] == ""


def test_graph_reports_cycles_instead_of_hiding_them(workspace):
    looped = [
        registry.Entry("alpha", "Library", 1, 1, "alpha", "", "s4", ("gamma",)),
        registry.Entry("beta", "Library", 2, 2, "beta", "", "s4", ("alpha",)),
        registry.Entry("gamma", "Library", 2, 2, "gamma", "", "s4", ("beta",)),
        registry.Entry("delta", "Library", 3, 3, "delta", "", "s4", ("delta", "omega")),
        registry.Entry("free", "Library", 3, 3, "free", "", "s4"),
    ]
    out = q.graph(workspace, entries=looped)
    assert out["cycles"] == [["alpha", "beta", "gamma"], ["delta"]]
    assert out["order"] == ["free"]
    assert sorted(out["unordered"]) == ["alpha", "beta", "delta", "gamma"]
    assert out["unknown"] == ["omega"]
    assert {n["project"] for n in out["nodes"] if n["in_cycle"]} == {
        "alpha",
        "beta",
        "gamma",
        "delta",
    }
    text = q.graph(workspace, fmt="mermaid", entries=looped)["mermaid"]
    assert "class alpha,beta,gamma,delta cycle" in text


def test_graph_narrows_to_a_project_and_what_it_reaches(workspace):
    chain = [
        *ENTRIES,
        registry.Entry("gamma", "Library", 3, 3, "gamma", "", "s4", ("beta",)),
        registry.Entry("loner", "Library", 3, 3, "loner", "", "s4"),
    ]
    out = q.graph(workspace, "beta", entries=chain)
    assert out["order"] == ["alpha", "beta", "gamma"]
    with pytest.raises(q.QueryError, match="no project"):
        q.graph(workspace, "nobody")
    with pytest.raises(q.QueryError, match="format must be"):
        q.graph(workspace, fmt="dot")


def test_mermaid_is_a_graph_td_with_dependencies_above(workspace):
    text = q.graph(workspace, fmt="mermaid")["mermaid"]
    assert text.splitlines() == [
        "graph TD",
        '    alpha["alpha (2 open)"]',
        '    beta["beta (2 open)"]',
        "    alpha --> beta",
    ]


# -- plan -------------------------------------------------------------------------------------------


def test_plan_lists_ready_blocked_wip_and_oldest_with_reasons(
    workspace, leases, monkeypatch
):
    # alpha is still a shell, so beta waits on it.
    monkeypatch.setattr(
        registry, "state", lambda _, e: registry.State("Shell", 0, 0, "", "")
    )
    out = q.plan(workspace, {"alpha"})
    # a.load is leased; ready_rows counts a declared non-goal ([no]) as open, so a.never is listed too.
    assert [r["row_id"] for r in out["ready"]] == ["a.parse", "a.never"]
    assert all(r["why"] for r in out["ready"])
    on = {(b["project"], b["row_id"], b["on"]) for b in out["blocked"]}
    assert on == {
        ("beta", "", "dependency"),
        ("beta", "", "decision"),
        ("beta", "b.store", "lease block"),
    }
    assert all(b["why"] for b in out["blocked"])
    wip = {w["project"]: w for w in out["wip"]["projects"]}
    assert wip["alpha"]["live_leases"] == 1 and wip["alpha"]["active"] is True
    assert out["wip"]["ok"] is True and out["wip"]["limit"] == 2
    assert [o["row_id"] for o in out["oldest"]] == [
        "a.load",
        "a.parse",
        "b.anchor",
        "b.store",
    ]
    assert all("age unknown" in o["why"] for o in out["oldest"])
    assert q.plan(workspace, {"alpha"}, limit=1)["oldest_truncated"] is True


def test_plan_flags_a_broken_two_active_projects_rule(workspace, leases):
    out = q.plan(workspace, {"beta"})
    assert out["wip"]["ok"] is False
    assert "leases outside Active: alpha" in out["wip"]["why"]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_plan_dates_open_rows_from_git_blame(workspace):
    folder = workspace / "alpha"

    def git(*args: str, date: str = "2026-01-01T00:00:00Z") -> None:
        env = {
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "PATH": __import__("os").environ["PATH"],
        }
        subprocess.run(
            ["git", "-C", str(folder), *args], check=True, env=env, capture_output=True
        )

    git("init", "-q")
    git("add", "ROADMAP.md")
    git("commit", "-q", "-m", "rows")
    out = q.plan(workspace, {"alpha"}, leases=EMPTY)
    dated = [o for o in out["oldest"] if o["since"]]
    assert [o["row_id"] for o in dated] == ["a.load", "a.parse"]
    assert dated[0]["since"] == "2026-01-01" and "git blame" in dated[0]["why"]
    assert out["oldest"][0]["project"] == "alpha", "dated rows come before undated ones"


# -- the MCP tools ----------------------------------------------------------------------------------

NEW_TOOLS = ("query", "graph", "plan")


def _tools():
    pytest.importorskip("mcp")
    import anyio

    from projecthub.mcp import read

    app = read.build()
    listed = anyio.run(app.list_tools)
    return app, {t.name: t for t in getattr(listed, "tools", listed)}


def test_new_tools_are_read_only_with_output_schemas():
    _, tools = _tools()
    for name in NEW_TOOLS:
        tool = tools[name]
        assert tool.annotations is not None and tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.output_schema, f"{name} declares no outputSchema"
        assert "write" not in tool.input_schema.get("properties", {})


def _snapshot(*folders: pathlib.Path) -> dict[str, str]:
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for folder in folders
        if folder.exists()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def test_no_new_tool_writes_anything(workspace, leases, tmp_path):
    import anyio

    app, _ = _tools()
    before = _snapshot(tmp_path)
    calls = [
        ("query", {"state": "open", "claimed": False}),
        ("query", {"text": "anchor", "limit": 1}),
        ("graph", {}),
        ("graph", {"project": "beta", "format": "mermaid"}),
        ("plan", {"active": ["alpha"]}),
    ]
    for name, args in calls:
        result = anyio.run(app.call_tool, name, args)
        assert result.structured_content, name
    assert _snapshot(tmp_path) == before, "a read tool changed a file"
    got = anyio.run(app.call_tool, "query", {"state": "open"}).structured_content
    assert got["total"] == 3 and got["truncated"] is False
    graph = anyio.run(app.call_tool, "graph", {"format": "mermaid"}).structured_content
    assert graph["mermaid"].startswith("graph TD\n")


def test_a_bad_filter_is_a_tool_error(workspace):
    import anyio
    from mcp.server.mcpserver.exceptions import ToolError

    app, _ = _tools()
    with pytest.raises(ToolError, match="state must be one of"):
        anyio.run(app.call_tool, "query", {"state": "closed"})
    with pytest.raises(ToolError, match="format must be"):
        anyio.run(app.call_tool, "graph", {"format": "dot"})


# -- the command line -------------------------------------------------------------------------------


def test_cli_query_prints_a_table_and_json(workspace, capsys):
    import json

    assert q.main(["query", "--state", "open", "--project", "beta"]) == 0
    text = capsys.readouterr().out
    assert "b.anchor" in text and "2 of 2 row(s)" in text and "a.load" not in text
    assert q.main(["query", "--limit", "1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["truncated"] is True and len(data["rows"]) == 1
    assert q.main(["query", "--project", "nobody"]) == 2


def test_cli_graph_prints_order_cycles_and_mermaid(workspace, capsys):
    assert q.main(["graph"]) == 0
    text = capsys.readouterr().out
    assert "alpha" in text and "beta" in text and "depends on" in text
    assert q.main(["graph", "--mermaid"]) == 0
    assert capsys.readouterr().out.startswith("graph TD\n")


def test_projecthub_dispatches_query_and_graph(workspace, capsys, monkeypatch):
    from projecthub import cli

    monkeypatch.setattr("sys.argv", ["projecthub", "graph", "--json"])
    assert cli.main() == 0
    assert '"order"' in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["projecthub", "query", "anchor"])
    assert cli.main() == 0
    assert "b.anchor" in capsys.readouterr().out
