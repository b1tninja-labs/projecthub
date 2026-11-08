"""The client's behaviour under scripted GitHub responses: no network, no token, no machine layout.

Each test pins a failure that was observed against the live API on 2026-11-07 or that the sync tool it
replaces had: a missing scope read as an empty board, `first: 200`, a closed project invisible to
`gh project list`, and a query built by string interpolation.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from projecthub.github import client as g


class Fake:
    """Scripted runner. Records every call; each graphql call pops the next reply."""

    def __init__(self, replies: list[dict[str, Any]], scopes: str | None = None):
        self.replies = list(replies)
        self.scopes = scopes
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv: list[str], stdin: str | None = None) -> g.Completed:
        self.calls.append((argv, stdin))
        if argv[:3] == ["api", "-i", "user"]:
            header = f"X-Oauth-Scopes: {self.scopes}\n" if self.scopes else ""
            return g.Completed(0, "HTTP/2.0 200 OK\n" + header + "\n{}", "")
        sent = json.loads(stdin or "{}").get("query", "")
        # The live API rejected an items query with one closing brace missing, and a fake that never
        # parses what it is sent let that pass every test.
        assert sent.count("{") == sent.count("}"), f"unbalanced braces in: {sent}"
        reply = self.replies.pop(0)
        return g.Completed(1 if "errors" in reply else 0, json.dumps(reply), "")

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(s) for a, s in self.calls if s and a[0] == "api"]


def page(nodes: list[Any], nxt: str | None = None) -> dict[str, Any]:
    return {
        "pageInfo": {"hasNextPage": nxt is not None, "endCursor": nxt},
        "nodes": nodes,
    }


def owner_reply(key: str, connection: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"repositoryOwner": {"projectV2": {key: connection}}}}


STATUS = g.Field("F1", "Status", "SINGLE_SELECT", options={"Todo": "o1", "Done": "o3"})
PROJECT = g.Project(
    "P1",
    1,
    "t",
    False,
    fields={
        "Status": STATUS,
        "Spec": g.Field("F2", "Spec", "TEXT"),
        "Effort": g.Field("F3", "Effort", "NUMBER"),
        "Labels": g.Field("F4", "Labels", "LABELS"),
    },
)


def test_missing_scope_is_an_error_naming_the_fix_not_an_empty_result():
    fake = Fake(
        [{"errors": [{"type": "INSUFFICIENT_SCOPES", "message": "no"}]}],
        scopes="repo, workflow",
    )
    with pytest.raises(g.MissingScope) as caught:
        g.Projects(fake).graphql("{ viewer { id } }")
    assert "gh auth refresh -s project" in str(caught.value)
    assert "repo" in str(caught.value)


def test_require_uses_the_header_and_treats_a_project_scope_as_enough():
    with pytest.raises(g.MissingScope):
        g.Projects(Fake([], scopes="repo")).require()
    g.Projects(Fake([], scopes="repo, project")).require(write=True)
    with pytest.raises(g.MissingScope):
        g.Projects(Fake([], scopes="repo, read:project")).require(write=True)


def test_unknown_scopes_are_permitted_so_the_api_decides():
    g.Projects(Fake([], scopes=None)).require(write=True)


def test_owner_is_a_variable_never_part_of_the_query_text():
    hostile = 'a"b) { id } #'
    fake = Fake([owner_reply("fields", page([]))], scopes="project")
    fake.replies = [
        {"data": {"repositoryOwner": {"projectsV2": page([])}}},
    ]
    g.Projects(fake).list_projects(hostile)
    body = fake.bodies()[0]
    assert body["variables"]["owner"] == hostile
    assert hostile not in body["query"]


def test_pagination_walks_every_page_and_never_asks_for_more_than_100():
    one = {"id": "A", "number": 1, "title": "x", "closed": True, "url": "u"}
    two = {"id": "B", "number": 2, "title": "y", "closed": False, "url": "v"}
    fake = Fake(
        [
            {"data": {"repositoryOwner": {"projectsV2": page([one], "c1")}}},
            {"data": {"repositoryOwner": {"projectsV2": page([two])}}},
        ],
        scopes="project",
    )
    found = g.Projects(fake).list_projects("me")
    assert [p.number for p in found] == [1, 2]
    assert found[0].closed, "a closed project must be listed"
    sent = fake.bodies()
    assert [b["variables"]["after"] for b in sent] == [None, "c1"]
    assert all(
        "first: 100" in b["query"] and "first: 200" not in b["query"] for b in sent
    )


def test_project_resolves_options_and_iterations_by_name():
    nodes = [
        {
            "id": "F1",
            "name": "Status",
            "dataType": "SINGLE_SELECT",
            "options": [{"id": "o1", "name": "Todo"}],
        },
        {
            "id": "F9",
            "name": "Sprint",
            "dataType": "ITERATION",
            "configuration": {"iterations": [{"id": "it1", "title": "Week 1"}]},
        },
        {"id": "F2", "name": "Spec", "dataType": "TEXT"},
    ]
    reply = {
        "data": {
            "repositoryOwner": {
                "projectV2": {
                    "id": "P1",
                    "number": 1,
                    "title": "t",
                    "closed": False,
                    "url": "u",
                    "fields": page(nodes),
                }
            }
        }
    }
    project = g.Projects(Fake([reply], scopes="project")).project("me", 1)
    assert project.fields["Status"].options == {"Todo": "o1"}
    assert project.fields["Sprint"].iterations == {"Week 1": "it1"}


def test_a_missing_project_raises_not_found():
    fake = Fake([{"data": {"repositoryOwner": {"projectV2": None}}}], scopes="project")
    with pytest.raises(g.NotFound):
        g.Projects(fake).project("me", 99)


def test_items_keep_redacted_entries_and_read_values_by_field_name():
    nodes = [
        {
            "id": "I1",
            "type": "ISSUE",
            "content": {"__typename": "Issue", "id": "N1", "url": "u1", "title": "T"},
            "fieldValues": {
                "nodes": [
                    {
                        "__typename": "ProjectV2ItemFieldSingleSelectValue",
                        "name": "Todo",
                        "field": {"name": "Status"},
                    },
                    {
                        "__typename": "ProjectV2ItemFieldNumberValue",
                        "number": 3.0,
                        "field": {"name": "Effort"},
                    },
                    {},
                ]
            },
        },
        {"id": "I2", "type": "REDACTED", "content": None, "fieldValues": {"nodes": []}},
    ]
    fake = Fake([owner_reply("items", page(nodes))], scopes="project")
    items = g.Projects(fake).items("me", 1)
    assert [i.type for i in items] == ["ISSUE", "REDACTED"]
    assert items[0].values == {"Status": "Todo", "Effort": 3.0}
    assert items[0].content_id == "N1" and items[1].content_id == ""


def test_encode_single_select_lists_valid_options_on_a_miss():
    assert g.encode_value(STATUS, "Done") == {"singleSelectOptionId": "o3"}
    with pytest.raises(g.UnknownOption) as caught:
        g.encode_value(STATUS, "done")
    assert "['Done', 'Todo']" in str(caught.value)


def test_encode_refuses_fields_that_belong_to_the_issue():
    with pytest.raises(g.GhProjectsError, match="issue or pull request"):
        g.encode_value(PROJECT.fields["Labels"], "bug")


def test_set_values_validates_every_name_before_sending_anything():
    fake = Fake([], scopes="project")
    bad = [g.Change("I1", "Status", "Todo"), g.Change("I1", "Status", "Nope")]
    with pytest.raises(g.UnknownOption):
        g.Projects(fake).set_values(PROJECT, bad)
    assert fake.bodies() == [], "nothing may be written when any change is invalid"


def test_set_values_batches_and_reports_a_partial_failure_per_change():
    changes = [g.Change(f"I{n}", "Spec", f"s{n}") for n in range(50)]
    fake = Fake(
        [
            {"data": {}},
            {
                "data": {"m3": None},
                "errors": [{"path": ["m3"], "message": "item is archived"}],
            },
            {"data": {}},
        ],
        scopes="project",
    )
    outcomes = g.Projects(fake).set_values(PROJECT, changes)
    sizes = [b["query"].count("updateProjectV2ItemFieldValue(") for b in fake.bodies()]
    assert sizes == [g.FIELD_BATCH, g.FIELD_BATCH, 50 - 2 * g.FIELD_BATCH]
    failed = [o for o in outcomes if not o.ok]
    assert len(failed) == 1
    assert (
        failed[0].change.item_id == f"I{g.FIELD_BATCH + 3}"
        and failed[0].error == "item is archived"
    )


def test_none_clears_instead_of_writing():
    fake = Fake([{"data": {}}], scopes="project")
    g.Projects(fake).set_values(PROJECT, [g.Change("I1", "Spec", None)])
    assert "clearProjectV2ItemFieldValue" in fake.bodies()[0]["query"]


def test_add_items_maps_content_to_item_and_survives_one_refusal():
    fake = Fake(
        [
            {
                "data": {"a0": {"item": {"id": "IT0"}}, "a1": None},
                "errors": [{"path": ["a1"], "message": "cannot add"}],
            }
        ],
        scopes="project",
    )
    assert g.Projects(fake).add_items("P1", ["C0", "C1"]) == {"C0": "IT0"}


def test_the_rate_guard_stops_before_a_write_that_would_not_finish():
    fake = Fake(
        [{"data": {"rateLimit": {"remaining": 12, "resetAt": "2026-11-07T05:00:00Z"}}}],
        scopes="project",
    )
    with pytest.raises(g.RateLimited, match="2026-11-07T05:00:00Z"):
        g.Projects(fake).guard(needed=100)


def test_unreadable_output_is_an_error_not_an_empty_dict():
    def broken(argv: list[str], stdin: str | None = None) -> g.Completed:
        return g.Completed(1, "", "gh: boom")

    with pytest.raises(g.GhProjectsError, match="boom"):
        g.Projects(broken).graphql("{ viewer { id } }")


def test_there_is_no_delete_anywhere_on_the_client():
    assert [n for n in dir(g.Projects) if "delete" in n.lower()] == []


def test_create_project_refuses_a_title_a_closed_project_already_has():
    listed = page(
        [{"id": "P2", "number": 2, "title": "Board", "closed": True, "url": ""}]
    )
    fake = Fake([{"data": {"repositoryOwner": {"projectsV2": listed}}}])
    with pytest.raises(g.GhProjectsError, match=r"#2 \(closed\)"):
        g.Projects(fake).create_project("me", "Board")
    assert not any("createProjectV2" in b["query"] for b in fake.bodies())


def test_create_project_sends_owner_and_title_as_variables():
    made = {"id": "P5", "number": 5, "title": "New", "closed": False, "url": "u"}
    fake = Fake(
        [
            {"data": {"repositoryOwner": {"projectsV2": page([])}}},
            {"data": {"repositoryOwner": {"id": "U1"}}},
            {"data": {"createProjectV2": {"projectV2": made}}},
        ]
    )
    assert g.Projects(fake).create_project("me", "New").number == 5
    sent = fake.bodies()[-1]
    assert sent["variables"] == {"i": {"ownerId": "U1", "title": "New"}}
    assert "New" not in sent["query"]


def test_linked_ids_read_every_page():
    def reply(ids: list[str], nxt: str | None) -> dict[str, Any]:
        nodes = [{"id": i} for i in ids]
        return {"data": {"node": {"subIssues": page(nodes, nxt)}}}

    fake = Fake([reply(["a"], "c1"), reply(["b"], None)])
    assert g.Projects(fake).linked_ids("I1", "subIssues") == {"a", "b"}
    assert [b["variables"]["after"] for b in fake.bodies()] == [None, "c1"]


def test_linked_ids_refuse_an_unknown_connection():
    with pytest.raises(g.GhProjectsError):
        g.Projects(Fake([])).linked_ids("I1", "comments")


def test_an_item_whose_field_values_span_pages_is_marked_truncated():
    nodes = [
        {
            "id": "I1",
            "type": "ISSUE",
            "content": {"__typename": "Issue", "id": "N1", "url": "u1", "title": "T"},
            "fieldValues": {"pageInfo": {"hasNextPage": True}, "nodes": []},
        },
        {"id": "I2", "type": "ISSUE", "content": None, "fieldValues": {"nodes": []}},
    ]
    fake = Fake([owner_reply("items", page(nodes))], scopes="project")
    items = g.Projects(fake).items("me", 1)
    assert [i.truncated for i in items] == [True, False]
    assert "pageInfo { hasNextPage }" in fake.bodies()[0]["query"]


def test_rest_project_paths_follow_the_owner_type():
    calls: list[list[str]] = []

    def run(argv: list[str], stdin: str | None = None) -> g.Completed:
        calls.append(argv)
        if argv[:3] == ["api", "-i", "user"]:
            return g.Completed(
                0, "HTTP/2.0 200 OK\nX-Oauth-Scopes: project, repo\n\n{}", ""
            )
        if argv[-2:] == ["--jq", ".type"]:
            return g.Completed(
                0, "Organization\n" if "labs" in argv[1] else "User\n", ""
            )
        return g.Completed(0, "[]", "")

    client = g.Projects(run)
    client.rest_field_ids("b1tninja-labs", 1)
    client.rest_field_ids("b1tninja-labs", 1)
    client.rest_field_ids("b1tninja", 4)
    paths = [c[1] for c in calls if "projectsV2" in c[1]]
    assert paths[0].startswith("orgs/b1tninja-labs/") and paths[2].startswith(
        "users/b1tninja/"
    )
    assert sum(c[-2:] == ["--jq", ".type"] for c in calls) == 2, (
        "the owner type is asked once per owner"
    )
