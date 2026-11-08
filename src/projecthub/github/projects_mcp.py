"""A read-only MCP interface to GitHub Projects (v2), with a portfolio overview on top.

Why read-only. The board is a projection of ROADMAP.md and PORTFOLIO.md, and every write path that could
move it (sync_roadmap.py, board.py, planner.py's status and claim commands) checks evidence first. An MCP
write tool would let an agent drag a card to Done with nothing behind it, so writes stay in those CLIs
(the MCP-writes decision, docs/research-mcp-planning-quality.md slice 1). What an agent needs from the board is the read side, and the read side is where GitHub's own
MCP server is thinnest: titles only unless field ids are passed, no hierarchy, no dependency links, no
notion of which item kind it is looking at.

Two toolsets, selected with PROJECTS_MCP_TOOLSETS (default "projects,portfolio"):

- projects: generic and owner-agnostic - auth, list, schema, items with field filters, and one item's full
  context (parent, sub-issues and their progress, blocked-by and blocking, board fields). This half has no
  knowledge of the forge and is the part that can leave as its own package.
- portfolio: reads the Level / State / Active / Wave fields board.py lays out, and answers "where does the
  portfolio stand": items per level, waves and their projects, open decisions, what is blocked, and whether
  the two-active-projects rule holds.

Protocol shape (MCP 2026-07-28): every tool declares readOnlyHint and an outputSchema and returns
structuredContent with a text mirror; a failure is an isError result naming the fix, never an empty
success; lists come back in a stable order; and any connection capped at one page says so with a
`truncated` flag instead of silently dropping the rest.

Run: python projects_mcp.py   (speaks MCP on stdio; register it with: claude mcp add gh-projects -- python
D:/code/sdk-forge/projects_mcp.py)
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from collections.abc import Sequence
from typing import Any, TypedDict

from projecthub.github.client import GhProjectsError, Item, Projects

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
    "sdk-forge/sync_roadmap.py or board.py, or the sdk-forge planner's status and claim commands. Treat a "
    "card's Status as a claim, not evidence: a row is done only when its ROADMAP row reads [ok] with a named "
    "test. When a result says truncated, the list is incomplete: say so rather than reasoning from it."
)


# -- outputs (each becomes the tool's outputSchema) ---------------------------------------------------


class AuthOut(TypedDict):
    scopes: list[str] | None
    projects_readable: bool
    fix: str | None
    rate_limit_remaining: int
    resets_at: str


class ProjectRef(TypedDict):
    number: int
    title: str
    closed: bool
    url: str


class ProjectsOut(TypedDict):
    owner: str
    projects: list[ProjectRef]


class SchemaOut(TypedDict):
    board: str
    fields: dict[str, dict[str, Any]]
    views: list[str]


class ItemsOut(TypedDict):
    board: str
    matched: int
    returned: int
    truncated: bool
    items: list[dict[str, Any]]


class ContextOut(TypedDict):
    title: str
    url: str
    state: str
    repository: str
    milestone: str | None
    assignees: list[str]
    parent: dict[str, Any] | None
    sub_issues: dict[str, Any] | None
    children: list[dict[str, Any]]
    blocked_by: list[dict[str, Any]]
    blocking: list[dict[str, Any]]
    boards: list[dict[str, Any]]
    source_marker: str
    truncated: list[str]


class OverviewOut(TypedDict):
    board: str
    items_by_level: dict[str, int]
    projects_by_state: dict[str, int]
    active_projects: list[str]
    active_rule: str
    waves: dict[str, list[str]]
    open_decisions: list[str]
    obligations_by_status: dict[str, int]
    blocked: list[dict[str, Any]]
    truncated_items: int


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
    values = {
        k: item.values[k]
        for k in sorted(item.values)
        if k in keep and item.values[k] not in (None, "")
    }
    out: dict[str, object] = {
        "title": item.title,
        "url": item.url,
        "type": item.type,
        **values,
    }
    if item.truncated:
        out["fields_truncated"] = True
    return out


def select(items: Sequence[Item], filters: dict[str, str]) -> list[Item]:
    """Items whose field values equal every given filter, case-insensitively. An empty filter is ignored."""
    wanted = {k: v.lower() for k, v in filters.items() if v}
    return [
        i
        for i in items
        if all(str(i.values.get(k, "")).lower() == v for k, v in wanted.items())
    ]


def overview(items: Sequence[Item]) -> dict[str, Any]:
    """Where the portfolio stands, from the fields board.py writes. Every list is sorted."""
    level = Counter(str(i.values.get("Level", "(none)")) for i in items)
    projects = [i for i in items if i.values.get("Level") == "Project"]
    active = sorted(i.title for i in projects if i.values.get("Active") == "Active")
    by_state = Counter(str(i.values.get("State", "(none)")) for i in projects)
    waves: dict[str, list[str]] = {}
    for p in projects:
        waves.setdefault(str(p.values.get("Wave") or "unsequenced"), []).append(
            f"{p.title} [{p.values.get('State', '?')}]"
        )
    decisions = sorted(
        i.title
        for i in items
        if i.values.get("Level") == "Decision" and i.values.get("Status") != "Done"
    )
    rows = [i for i in items if i.values.get("Level") == "Obligation"]
    row_status = Counter(str(i.values.get("Status", "(none)")) for i in rows)
    blocked = sorted(
        (compact(i) for i in items if i.values.get("Status") == "Blocked"),
        key=lambda r: str(r["url"]),
    )
    return {
        "items_by_level": dict(sorted(level.items())),
        "projects_by_state": dict(sorted(by_state.items())),
        "active_projects": active,
        "active_rule": (
            f"{len(active)} of {ACTIVE_LIMIT} active slots used"
            if len(active) <= ACTIVE_LIMIT
            else f"VIOLATED: {len(active)} projects marked Active, the rule is {ACTIVE_LIMIT}"
        ),
        "waves": {k: sorted(v) for k, v in sorted(waves.items())},
        "open_decisions": decisions,
        "obligations_by_status": dict(sorted(row_status.items())),
        "blocked": blocked,
        "truncated_items": sum(1 for i in items if i.truncated),
    }


CONNECTIONS = ("subIssues", "blockedBy", "blocking", "assignees", "projectItems")

CONTEXT_QUERY = """
query($url: URI!) { resource(url: $url) { __typename
  ... on Issue {
    title number state url body repository { nameWithOwner }
    parent { title url state }
    subIssuesSummary { total completed percentCompleted }
    subIssues(first: 50) { pageInfo { hasNextPage } nodes { title url state } }
    blockedBy(first: 20) { pageInfo { hasNextPage } nodes { title url state } }
    blocking(first: 20) { pageInfo { hasNextPage } nodes { title url state } }
    milestone { title }
    assignees(first: 10) { pageInfo { hasNextPage } nodes { login } }
    projectItems(first: 10) { pageInfo { hasNextPage } nodes {
      project { number title owner { ... on User { login } ... on Organization { login } } }
      fieldValues(first: 30) { pageInfo { hasNextPage } nodes { __typename
        ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
        ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
        ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
        ... on ProjectV2ItemFieldIterationValue { title field { ... on ProjectV2FieldCommon { name } } }
        ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
      } } } }
  } } }
"""


def _more(block: object) -> bool:
    return bool(
        ((block or {}) if isinstance(block, dict) else {})
        .get("pageInfo", {})
        .get("hasNextPage")
    )


def shape_context(node: dict[str, Any]) -> dict[str, Any]:
    """The issue's place in the tree and on every board it is on. Raises ValueError for a non-issue."""
    if not node or node.get("__typename") != "Issue":
        kind = node.get("__typename") if node else "nothing"
        raise ValueError(f"the URL resolved to {kind}, not an issue")

    def nodes(key: str) -> list[dict[str, Any]]:
        return list((node.get(key) or {}).get("nodes") or [])

    truncated = [key for key in CONNECTIONS if _more(node.get(key))]
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
        board = f"{(project.get('owner') or {}).get('login', '?')}#{project.get('number')} {project.get('title', '')}"
        if _more(entry.get("fieldValues")):
            truncated.append(f"fieldValues on {board}")
        boards.append({"board": board, "fields": dict(sorted(values.items()))})
    body = node.get("body") or ""
    return {
        "title": str(node.get("title") or ""),
        "url": str(node.get("url") or ""),
        "state": str(node.get("state") or ""),
        "repository": str((node.get("repository") or {}).get("nameWithOwner") or ""),
        "milestone": (node.get("milestone") or {}).get("title"),
        "assignees": sorted(a["login"] for a in nodes("assignees")),
        "parent": node.get("parent"),
        "sub_issues": node.get("subIssuesSummary"),
        "children": nodes("subIssues"),
        "blocked_by": nodes("blockedBy"),
        "blocking": nodes("blocking"),
        "boards": boards,
        "source_marker": next(
            (line for line in body.splitlines() if line.startswith("<!-- ")), ""
        ),
        "truncated": truncated,
    }


# -- the server ---------------------------------------------------------------------------------------


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
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    gh = client or Projects()
    app = MCPServer(name="gh-projects", instructions=INSTRUCTIONS)
    enabled = toolsets()
    # Reads only, safe to repeat, and talking to GitHub - an open world - so a client may cache nothing.
    read = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    )

    def guarded(call):  # type: ignore[no-untyped-def]
        """A GitHub failure becomes an isError result whose text names the fix."""
        try:
            return call()
        except (GhProjectsError, ValueError) as error:
            raise ToolError(str(error)) from error

    if "projects" in enabled:

        @app.tool(
            description="Granted token scopes and GraphQL rate limit; says how to fix a missing scope.",
            annotations=read,
            structured_output=True,
        )
        def projects_auth() -> AuthOut:
            def run() -> AuthOut:
                scopes = gh.scopes()
                remaining, reset = gh.rate_limit()
                ok = scopes is None or "project" in scopes or "read:project" in scopes
                return {
                    "scopes": sorted(scopes) if scopes is not None else None,
                    "projects_readable": ok,
                    "fix": None if ok else "gh auth refresh -s project",
                    "rate_limit_remaining": remaining,
                    "resets_at": reset,
                }

            return guarded(run)  # type: ignore[no-any-return]

        @app.tool(
            description="Every project an owner has, closed ones included, by number.",
            annotations=read,
            structured_output=True,
        )
        def projects_list(owner: str = DEFAULT_OWNER) -> ProjectsOut:
            def run() -> ProjectsOut:
                found = sorted(gh.list_projects(owner), key=lambda p: p.number)
                return {
                    "owner": owner,
                    "projects": [
                        {
                            "number": p.number,
                            "title": p.title,
                            "closed": p.closed,
                            "url": p.url,
                        }
                        for p in found
                    ],
                }

            return guarded(run)  # type: ignore[no-any-return]

        @app.tool(
            description="A board's fields (type, options, iterations) and view names.",
            annotations=read,
            structured_output=True,
        )
        def project_schema(
            owner: str = DEFAULT_OWNER, number: int = DEFAULT_NUMBER
        ) -> SchemaOut:
            def run() -> SchemaOut:
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
                        for f in sorted(project.fields.values(), key=lambda f: f.name)
                    },
                    "views": sorted(gh.view_names(owner, number)),
                }

            return guarded(run)  # type: ignore[no-any-return]

        @app.tool(
            description=(
                "Items on a board, filtered by field values (exact, case-insensitive), e.g. level='Obligation', "
                "status='Ready', repository='b1tninja/pkix'. Returns compact rows in board order; `fields` picks "
                "columns. truncated=true means more items matched than were returned."
            ),
            annotations=read,
            structured_output=True,
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
        ) -> ItemsOut:
            def run() -> ItemsOut:
                chosen = select(
                    gh.items(owner, number),
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
                    "truncated": len(chosen) > cap,
                    "items": [compact(i, fields or ()) for i in chosen[:cap]],
                }

            return guarded(run)  # type: ignore[no-any-return]

        @app.tool(
            description=(
                "One issue in full context, by URL: parent, sub-issues and their progress, blocked-by and blocking, "
                "milestone, assignees, and its fields on every board it is on. `truncated` names any connection "
                "that had more than one page."
            ),
            annotations=read,
            structured_output=True,
        )
        def item_context(url: str) -> ContextOut:
            return guarded(  # type: ignore[no-any-return]
                lambda: shape_context(
                    gh.graphql(CONTEXT_QUERY, {"url": url}).get("resource") or {}
                )
            )

    if "portfolio" in enabled:

        @app.tool(
            description=(
                "Where the portfolio stands on its board: items per level, projects per state and wave, open "
                "decisions, blocked items, and whether the two-active-projects rule holds."
            ),
            annotations=read,
            structured_output=True,
        )
        def portfolio_overview(
            owner: str = DEFAULT_OWNER, number: int = DEFAULT_NUMBER
        ) -> OverviewOut:
            return guarded(  # type: ignore[no-any-return]
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
