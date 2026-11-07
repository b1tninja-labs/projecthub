"""The projects MCP server: read-only by construction, and its views computed without the network."""

from __future__ import annotations

import json

import pytest

import ghprojects as g
import projects_mcp as m


def item(title: str, **values: object) -> g.Item:
    url = (
        f"https://github.com/b1tninja/{values.pop('repo', 'pkix')}/issues/{len(title)}"
    )
    return g.Item(f"I-{title}", "ISSUE", f"N-{title}", url, title, dict(values))


ITEMS = [
    item("Wave 1: chokepoint", Level="Wave", Status="Backlog"),
    item(
        "pkix: path validation",
        Level="Project",
        State="Shell",
        Wave="Wave 2",
        Active="Active",
    ),
    item(
        "asn1core: codec",
        Level="Project",
        State="Implemented",
        Wave="Wave 1",
        Active="Active",
    ),
    item("trustatlas: map", Level="Project", State="Plan only"),
    item("Decision 2: certledger", Level="Decision", Status="Backlog"),
    item("Decision 1: asn1core", Level="Decision", Status="Done"),
    item("anchors.load_anchor()", Level="Obligation", Status="Ready"),
    item("chain.candidates()", Level="Obligation", Status="Blocked", repo="ctspy"),
]


def test_overview_counts_levels_waves_decisions_and_the_active_rule():
    got = m.overview(ITEMS)
    assert got["items_by_level"] == {
        "Wave": 1,
        "Project": 3,
        "Decision": 2,
        "Obligation": 2,
    }
    assert got["projects_by_state"] == {"Shell": 1, "Implemented": 1, "Plan only": 1}
    assert got["open_decisions"] == ["Decision 2: certledger"], (
        "a Done decision is not open"
    )
    assert got["active_rule"] == "2 of 2 active slots used"
    assert "unsequenced" in got["waves"] and got["waves"]["Wave 2"] == [
        "pkix: path validation [Shell]"
    ]
    assert [b["title"] for b in got["blocked"]] == ["chain.candidates()"]


def test_the_active_rule_is_reported_violated_not_silently_capped():
    third = item("ctspy: ct", Level="Project", State="Implemented", Active="Active")
    assert m.overview([*ITEMS, third])["active_rule"].startswith("VIOLATED: 3")


def test_select_is_exact_case_insensitive_and_ignores_empty_filters():
    assert [
        i.title for i in m.select(ITEMS, {"Level": "obligation", "Status": ""})
    ] == [
        "anchors.load_anchor()",
        "chain.candidates()",
    ]
    assert m.select(ITEMS, {"Level": "Oblig"}) == [], "a prefix is not a match"


def test_compact_keeps_only_set_fields():
    row = m.compact(item("x", Level="Project", State="", Wave=None))
    assert row["Level"] == "Project" and "State" not in row and "Wave" not in row


def test_context_flattens_tree_links_and_board_fields():
    node = {
        "__typename": "Issue",
        "title": "pkix: path validation",
        "url": "u",
        "state": "OPEN",
        "body": "<!-- portfolio:project:pkix -->\nrest",
        "repository": {"nameWithOwner": "b1tninja/sdk-forge"},
        "parent": {"title": "Wave 2", "url": "w", "state": "OPEN"},
        "subIssuesSummary": {"total": 7, "completed": 0, "percentCompleted": 0},
        "subIssues": {"nodes": [{"title": "M1", "url": "e", "state": "OPEN"}]},
        "blockedBy": {"nodes": [{"title": "rpkit", "url": "r", "state": "OPEN"}]},
        "blocking": {"nodes": []},
        "milestone": None,
        "assignees": {"nodes": [{"login": "b1tninja"}]},
        "projectItems": {
            "nodes": [
                {
                    "project": {
                        "number": 4,
                        "title": "Protocol SDK portfolio",
                        "owner": {"login": "b1tninja"},
                    },
                    "fieldValues": {
                        "nodes": [
                            {
                                "__typename": "ProjectV2ItemFieldSingleSelectValue",
                                "name": "Project",
                                "field": {"name": "Level"},
                            },
                            {
                                "__typename": "ProjectV2ItemFieldNumberValue",
                                "number": 0.0,
                                "field": {"name": "Rows done"},
                            },
                            {},
                        ]
                    },
                }
            ]
        },
    }
    got = m.shape_context(node)
    assert got["source_marker"] == "<!-- portfolio:project:pkix -->"
    assert got["blocked_by"][0]["title"] == "rpkit"
    assert got["sub_issues"]["total"] == 7 and got["assignees"] == ["b1tninja"]
    assert got["boards"] == [
        {
            "board": "b1tninja#4 Protocol SDK portfolio",
            "fields": {"Level": "Project", "Rows done": 0.0},
        }
    ]
    assert got["truncated"] == []
    with pytest.raises(ValueError, match="PullRequest"):
        m.shape_context({"__typename": "PullRequest"})
    with pytest.raises(ValueError, match="nothing"):
        m.shape_context({})


def test_context_names_every_connection_that_had_more_than_one_page():
    more = {"pageInfo": {"hasNextPage": True}, "nodes": []}
    node = {
        "__typename": "Issue",
        "subIssues": more,
        "blockedBy": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "blocking": more,
        "assignees": more,
        "projectItems": {
            "pageInfo": {"hasNextPage": True},
            "nodes": [
                {
                    "project": {"number": 4, "title": "P", "owner": {"login": "o"}},
                    "fieldValues": {"pageInfo": {"hasNextPage": True}, "nodes": []},
                }
            ],
        },
    }
    got = m.shape_context(node)["truncated"]
    assert got == [
        "subIssues",
        "blocking",
        "assignees",
        "projectItems",
        "fieldValues on o#4 P",
    ]


def test_an_item_with_more_field_values_than_a_page_says_so():
    cut = g.Item("I", "ISSUE", "N", "u", "t", {"Level": "Obligation"}, truncated=True)
    assert m.compact(cut)["fields_truncated"] is True
    assert "fields_truncated" not in m.compact(ITEMS[0])
    assert m.overview([*ITEMS, cut])["truncated_items"] == 1


class FakeClient:
    def items(self, owner, number):
        return ITEMS

    def scopes(self):
        return ("repo",)

    def rate_limit(self):
        return (4000, "2026-11-07T05:00:00Z")

    def list_projects(self, owner):
        raise g.MissingScope("read:project", ["repo"])


def tools(monkeypatch, sets: str):
    pytest.importorskip("mcp")
    import anyio

    monkeypatch.setenv("PROJECTS_MCP_TOOLSETS", sets)
    app = m.build(FakeClient())  # type: ignore[arg-type]
    listed = anyio.run(app.list_tools)
    return app, sorted(t.name for t in getattr(listed, "tools", listed))


def test_the_server_is_read_only_and_toolsets_select_tools(monkeypatch):
    _, names = tools(monkeypatch, "projects,portfolio")
    assert names == sorted(
        [
            "projects_auth",
            "projects_list",
            "project_schema",
            "project_items",
            "item_context",
            "portfolio_overview",
        ]
    )
    writes = (
        "set",
        "add",
        "create",
        "update",
        "delete",
        "move",
        "close",
        "claim",
        "write",
    )
    assert not [n for n in names if any(w in n for w in writes)], (
        "no write tool may exist"
    )
    _, generic = tools(monkeypatch, "projects")
    assert "portfolio_overview" not in generic


def test_every_tool_is_annotated_read_only_and_declares_an_output_schema(monkeypatch):
    import anyio

    pytest.importorskip("mcp")
    monkeypatch.setenv("PROJECTS_MCP_TOOLSETS", "projects,portfolio")
    listed = anyio.run(m.build(FakeClient()).list_tools)  # type: ignore[arg-type]
    for tool in getattr(listed, "tools", listed):
        assert tool.annotations is not None, tool.name
        assert (
            tool.annotations.read_only_hint is True
            and tool.annotations.destructive_hint is False
        ), tool.name
        assert tool.output_schema and tool.output_schema.get("type") == "object", (
            tool.name
        )


def test_items_report_truncation_when_more_matched_than_returned(monkeypatch):
    import anyio

    app, _ = tools(monkeypatch, "projects")
    got = anyio.run(app.call_tool, "project_items", {"limit": 2}).structured_content
    assert (
        got["matched"] == len(ITEMS)
        and got["returned"] == 2
        and got["truncated"] is True
    )
    whole = anyio.run(app.call_tool, "project_items", {"limit": 50}).structured_content
    assert whole["truncated"] is False


def test_a_failure_is_an_error_naming_the_fix_not_an_empty_list(monkeypatch):
    import anyio

    from mcp.server.mcpserver.exceptions import ToolError

    app, _ = tools(monkeypatch, "projects,portfolio")
    with pytest.raises(ToolError, match="gh auth refresh -s project"):
        anyio.run(app.call_tool, "projects_list", {"owner": "b1tninja"})
    auth = anyio.run(app.call_tool, "projects_auth", {}).structured_content
    assert (
        auth["projects_readable"] is False
        and auth["fix"] == "gh auth refresh -s project"
    )
    assert json.dumps(auth)  # structured content is plain JSON
