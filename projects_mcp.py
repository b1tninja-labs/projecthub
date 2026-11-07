"""A read-only MCP interface to GitHub Projects (v2), with a portfolio overview on top.

Why read-only. The board is a projection of ROADMAP.md and PORTFOLIO.md, and every write path that could
move it (sync_roadmap.py, board.py, planner.py's guarded row flip) checks evidence first. An MCP write tool
would let an agent drag a card to Done with nothing behind it, so writes stay in those CLIs (decision D23 in
docs/improvement-plan.md). What an agent needs from the board is the read side, and the read side is where
GitHub's own MCP server is thinnest: titles only unless field ids are passed, no hierarchy, no dependency
links, no notion of which item kind it is looking at.

Two toolsets, selected with PROJECTS_MCP_TOOLSETS (default "projects,portfolio"):

- projects: generic and owner-agnostic - auth, list, schema, items with field filters, and one item's full
  context (parent, sub-issues and their progress, blocked-by and blocking, board fields). This half has no
  knowledge of the forge and is the part that can leave as its own package.
- portfolio: reads the Level / State / Active / Wave fields board.py lays out, and answers "where does the
  portfolio stand": items per level, waves and their projects, open decisions, what is blocked, and whether
  the two-active-projects rule holds.

Every answer names its source (owner, project number, the query) and never returns an empty result for a
failure: a missing scope or project is an error string that says how to fix it.

Run: python projects_mcp.py   (speaks MCP on stdio; register it with: claude mcp add gh-projects -- python
D:/code/sdk-forge/projects_mcp.py)
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from ghprojects import GhProjectsError, Item, Projects

DEFAULT_OWNER = os.environ.get("PROJECTS_MCP_OWNER", "b1tninja")
DEFAULT_NUMBER = int(os.environ.get("PROJECTS_MCP_NUMBER", "4"))
ACTIVE_LIMIT = (
    2  # PORTFOLIO.md section 7: at most two projects carry real effort at once
)
MAX_ITEMS = 200

INSTRUCTIONS = (
    "Read-only views of GitHub Projects boards. The portfolio board (default owner and number) is a projection "
    "of PORTFOLIO.md and each repository's ROADMAP.md, laid out as Wave -> Project -> Milestone -> Obligation "
    "with a Level field. Nothing here changes the board: to move work, change the source file and run "
    "sdk-forge/sync_roadmap.py or board.py, or use the sdk-forge planner's guarded tools. Treat a card's Status "
    "as a claim, not evidence: a row is done only when its ROADMAP row reads [ok] with a named test."
)


# -- pure views over items (tested without the network) -----------------------------------------------


def compact(item: Item, fields: Sequence[str] = ()) -> dict[str, object]:
    """One item as a small dict: what an agent needs to decide, not the whole GraphQL node."""
    keep = fields or (
        "Level",
        "Status",
        "Priority",
        "Wave",
        "State",
        "Active",
        "Milestone",
        "Repository",
    )
    values = {k: v for k, v in item.values.items() if k in keep and v not in (None, "")}
    return {"title": item.title, "url": item.url, "type": item.type, **values}


def select(items: Sequence[Item], filters: dict[str, str]) -> list[Item]:
    """Items whose field values equal every given filter, case-insensitively. An empty filter is ignored."""
    wanted = {k: v.lower() for k, v in filters.items() if v}
    return [
        i
        for i in items
        if all(str(i.values.get(k, "")).lower() == v for k, v in wanted.items())
    ]


def overview(items: Sequence[Item]) -> dict[str, object]:
    """Where the portfolio stands, from the fields board.py writes. No field means "not laid out by board.py"."""
    level = Counter(str(i.values.get("Level", "(none)")) for i in items)
    projects = [i for i in items if i.values.get("Level") == "Project"]
    active = [i.title for i in projects if i.values.get("Active") == "Active"]
    by_state = Counter(str(i.values.get("State", "(none)")) for i in projects)
    waves: dict[str, list[str]] = {}
    for p in projects:
        waves.setdefault(str(p.values.get("Wave") or "unsequenced"), []).append(
            f"{p.title} [{p.values.get('State', '?')}]"
        )
    decisions = [
        i.title
        for i in items
        if i.values.get("Level") == "Decision" and i.values.get("Status") != "Done"
    ]
    rows = [i for i in items if i.values.get("Level") == "Obligation"]
    row_status = Counter(str(i.values.get("Status", "(none)")) for i in rows)
    blocked = [compact(i) for i in items if i.values.get("Status") == "Blocked"]
    return {
        "items_by_level": dict(level),
        "projects_by_state": dict(by_state),
        "active_projects": active,
        "active_rule": (
            f"{len(active)} of {ACTIVE_LIMIT} active slots used"
            if len(active) <= ACTIVE_LIMIT
            else f"VIOLATED: {len(active)} projects marked Active, the rule is {ACTIVE_LIMIT}"
        ),
        "waves": dict(sorted(waves.items())),
        "open_decisions": decisions,
        "obligations_by_status": dict(row_status),
        "blocked": blocked,
    }


CONTEXT_QUERY = """
query($url: URI!) { resource(url: $url) { __typename
  ... on Issue {
    title number state url body repository { nameWithOwner }
    parent { title url state }
    subIssuesSummary { total completed percentCompleted }
    subIssues(first: 50) { nodes { title url state } }
    blockedBy(first: 20) { nodes { title url state } }
    blocking(first: 20) { nodes { title url state } }
    milestone { title }
    assignees(first: 10) { nodes { login } }
    projectItems(first: 10) { nodes { project { number title owner { ... on User { login } ... on Organization { login } } }
      fieldValues(first: 30) { nodes { __typename
        ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
        ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
        ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
        ... on ProjectV2ItemFieldIterationValue { title field { ... on ProjectV2FieldCommon { name } } }
        ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
      } } } }
  } } }
"""


def shape_context(node: dict[str, Any]) -> dict[str, object]:
    """The issue's place in the tree and on every board it is on, flattened for an agent."""
    if not node or node.get("__typename") != "Issue":
        kind = node.get("__typename") if node else "nothing"
        return {"error": f"the URL resolved to {kind}, not an issue"}

    def nodes(key: str) -> list[dict[str, Any]]:
        return list((node.get(key) or {}).get("nodes") or [])

    boards = []
    for entry in nodes("projectItems"):
        values: dict[str, object] = {}
        for v in (entry.get("fieldValues") or {}).get("nodes") or []:
            name = (v.get("field") or {}).get("name")
            if name:
                values[name] = next(
                    (
                        v[k]
                        for k in ("text", "number", "name", "title", "date")
                        if k in v
                    ),
                    None,
                )
        project = entry.get("project") or {}
        boards.append(
            {
                "board": f"{(project.get('owner') or {}).get('login', '?')}#{project.get('number')} {project.get('title', '')}",
                "fields": values,
            }
        )
    body = node.get("body") or ""
    return {
        "title": node.get("title"),
        "url": node.get("url"),
        "state": node.get("state"),
        "repository": (node.get("repository") or {}).get("nameWithOwner"),
        "milestone": (node.get("milestone") or {}).get("title"),
        "assignees": [a["login"] for a in nodes("assignees")],
        "parent": node.get("parent"),
        "sub_issues": node.get("subIssuesSummary"),
        "children": nodes("subIssues"),
        "blocked_by": nodes("blockedBy"),
        "blocking": nodes("blocking"),
        "boards": boards,
        "source_marker": next(
            (line for line in body.splitlines() if line.startswith("<!-- ")), ""
        ),
    }


# -- the server ---------------------------------------------------------------------------------------


def _json(value: object) -> str:
    return json.dumps(value, indent=2, default=str)


def _safely(call: Callable[[], object]) -> str:
    """Errors come back as text naming the fix, never as an empty result."""
    try:
        return _json(call())
    except GhProjectsError as error:
        return _json({"error": str(error)})


def toolsets() -> set[str]:
    return {
        t.strip()
        for t in os.environ.get("PROJECTS_MCP_TOOLSETS", "projects,portfolio").split(
            ","
        )
        if t.strip()
    }


def build(client: Projects | None = None):  # type: ignore[no-untyped-def]
    """The MCP server. mcp is imported here so the module's pure views work without it installed."""
    from mcp.server.mcpserver import MCPServer

    gh = client or Projects()
    app = MCPServer(name="gh-projects", instructions=INSTRUCTIONS)
    enabled = toolsets()

    if "projects" in enabled:

        @app.tool(
            description="Granted token scopes and GraphQL rate limit; says how to fix a missing scope."
        )
        def projects_auth() -> str:
            def read() -> object:
                scopes = gh.scopes()
                remaining, reset = gh.rate_limit()
                ok = scopes is None or "project" in scopes or "read:project" in scopes
                return {
                    "scopes": scopes,
                    "projects_readable": ok,
                    "fix": None if ok else "gh auth refresh -s project",
                    "rate_limit_remaining": remaining,
                    "resets_at": reset,
                }

            return _safely(read)

        @app.tool(description="Every project an owner has, closed ones included.")
        def projects_list(owner: str = DEFAULT_OWNER) -> str:
            return _safely(
                lambda: [
                    {
                        "number": p.number,
                        "title": p.title,
                        "closed": p.closed,
                        "url": p.url,
                    }
                    for p in gh.list_projects(owner)
                ]
            )

        @app.tool(
            description="A board's fields (type, options, iterations) and view names."
        )
        def project_schema(
            owner: str = DEFAULT_OWNER, number: int = DEFAULT_NUMBER
        ) -> str:
            def read() -> object:
                project = gh.project(owner, number)
                return {
                    "board": f"{owner}#{number} {project.title}",
                    "fields": {
                        f.name: {
                            "type": f.data_type,
                            **({"options": sorted(f.options)} if f.options else {}),
                            **(
                                {"iterations": sorted(f.iterations)}
                                if f.iterations
                                else {}
                            ),
                        }
                        for f in project.fields.values()
                    },
                    "views": gh.view_names(owner, number),
                }

            return _safely(read)

        @app.tool(
            description=(
                "Items on a board, filtered by field values (exact, case-insensitive), e.g. level='Obligation', "
                "status='Ready', repository='b1tninja/pkix'. Returns compact rows; `fields` picks columns."
            )
        )
        def project_items(
            owner: str = DEFAULT_OWNER,
            number: int = DEFAULT_NUMBER,
            level: str = "",
            status: str = "",
            repository: str = "",
            wave: str = "",
            fields: list[str] | None = None,
            limit: int = 50,
        ) -> str:
            def read() -> object:
                items = gh.items(owner, number)
                chosen = select(
                    items,
                    {"Level": level, "Status": status, "Wave": wave},
                )
                if repository:
                    chosen = [
                        i
                        for i in chosen
                        if f"github.com/{repository.lower()}/" in i.url.lower()
                    ]
                cap = max(1, min(limit, MAX_ITEMS))
                return {
                    "board": f"{owner}#{number}",
                    "matched": len(chosen),
                    "returned": min(len(chosen), cap),
                    "items": [compact(i, fields or ()) for i in chosen[:cap]],
                }

            return _safely(read)

        @app.tool(
            description=(
                "One issue in full context, by URL: parent, sub-issues and their progress, blocked-by and blocking, "
                "milestone, assignees, and its fields on every board it is on."
            )
        )
        def item_context(url: str) -> str:
            return _safely(
                lambda: shape_context(
                    gh.graphql(CONTEXT_QUERY, {"url": url}).get("resource") or {}
                )
            )

    if "portfolio" in enabled:

        @app.tool(
            description=(
                "Where the portfolio stands on its board: items per level, projects per state and wave, open "
                "decisions, blocked items, and whether the two-active-projects rule holds."
            )
        )
        def portfolio_overview(
            owner: str = DEFAULT_OWNER, number: int = DEFAULT_NUMBER
        ) -> str:
            return _safely(
                lambda: {
                    "board": f"{owner}#{number}",
                    **overview(gh.items(owner, number)),
                }
            )

    return app


def main() -> None:
    import anyio

    anyio.run(build().run_stdio_async)


if __name__ == "__main__":
    print(
        "gh-projects: speaks MCP on stdio; run it from a client, not a terminal",
        file=sys.stderr,
    )
    main()
