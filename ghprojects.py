"""A typed client for GitHub Projects (v2), with no knowledge of ROADMAP.md.

The boundary is the point. Nothing here imports sync_roadmap, planner or verify, and nothing names an
owner, a path or a project: when this outgrows the forge it moves out whole. The roadmap projection lives
elsewhere and calls this.

Why it exists next to `sync_roadmap.graphql()`. That function prints an error and returns "", and its
caller reads "" as "the board is empty", so a missing token scope looked like an empty board. Here every
failure is a typed exception that names the fix. Two more defects it replaces, both observed against the
live API: `items(first: 200)` is rejected (a connection allows at most 100), and `gh project list` hides
closed projects, so an existence check built on it would create a second board.

Design rules, each one load-bearing:

- Values travel as GraphQL variables in a JSON body on stdin, never interpolated into query text, so no
  owner, title or option name can alter the query.
- The runner is injected. Tests script it and never touch the network or a token.
- Every list is paged to the end; a page is at most 100 (GitHub's ceiling).
- Writes are batched as aliased mutations, at most 50 per request, and a partial failure is reported per
  change instead of hiding behind the first error.
- Field values are addressed by name and resolved against the project's own schema. An unknown option is an
  error that lists the valid ones; nothing is matched loosely.
- There is deliberately no delete. A board can be rebuilt from its source; a human's deleted edit cannot.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

PAGE = 100
# 50 aliased addProjectV2ItemById calls came back HTTP 502 on 2026-11-07; 25 is the measured ceiling's half.
BATCH = 25
# Field writes are heavier than adds: a 50-alias updateProjectV2ItemFieldValue mutation came back HTTP 502
# twice running against the live API on 2026-11-07, and 20 went through.
FIELD_BATCH = 20
READ_SCOPES = ("read:project", "project")
WRITE_SCOPES = ("project",)
FIX_COMMAND = "gh auth refresh -s project"


# Failures worth retrying: the request never got a real answer.
TRANSIENT = (
    "HTTP 502",
    "HTTP 503",
    "HTTP 504",
    "TLS handshake timeout",
    "connection reset",
    "i/o timeout",
)


class GhProjectsError(Exception):
    """Base class: anything this module refuses or the API refuses."""


class MissingScope(GhProjectsError):
    def __init__(self, needed: str, granted: Sequence[str]) -> None:
        self.needed = needed
        self.granted = tuple(granted)
        super().__init__(
            f"token lacks the {needed!r} scope (has: {', '.join(granted) or 'none'}); "
            f"run: {FIX_COMMAND}"
        )


class NotFound(GhProjectsError):
    pass


class RateLimited(GhProjectsError):
    def __init__(self, remaining: int, reset_at: str) -> None:
        self.remaining = remaining
        self.reset_at = reset_at
        super().__init__(
            f"only {remaining} GraphQL points left until {reset_at}; stopping rather than half-finishing"
        )


class UnknownOption(GhProjectsError):
    pass


class Completed(NamedTuple):
    code: int
    out: str
    err: str


Runner = Callable[[list[str], str | None], Completed]


def gh_runner(argv: list[str], stdin: str | None = None) -> Completed:
    """Run the gh CLI. The keyring token is the only credential involved."""
    exe = shutil.which("gh")
    if not exe:
        raise GhProjectsError("gh is not on PATH; install it and run gh auth login")
    done = subprocess.run(  # noqa: S603
        [exe, *argv],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return Completed(done.returncode, done.stdout, done.stderr)


@dataclass(frozen=True)
class Field:
    id: str
    name: str
    data_type: str
    options: dict[str, str] = field(default_factory=dict)
    iterations: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Project:
    id: str
    number: int
    title: str
    closed: bool
    url: str = ""
    fields: dict[str, Field] = field(default_factory=dict)


@dataclass(frozen=True)
class Item:
    id: str
    type: str
    content_id: str
    url: str
    title: str
    values: dict[str, object] = field(default_factory=dict)
    # True when the item has more field values than one page carried: its `values` are then incomplete,
    # and a reader must say so rather than treat a missing field as unset.
    truncated: bool = False


@dataclass(frozen=True)
class Change:
    """One field write. value None clears the field."""

    item_id: str
    field_name: str
    value: object


@dataclass(frozen=True)
class Outcome:
    change: Change
    ok: bool
    error: str = ""


_FIELDS = """
nodes { __typename
  ... on ProjectV2FieldCommon { id name dataType }
  ... on ProjectV2SingleSelectField { options { id name } }
  ... on ProjectV2IterationField { configuration { iterations { id title } } }
}
"""

_VALUES = """
fieldValues(first: 30) { pageInfo { hasNextPage } nodes { __typename
  ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
  ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
  ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
  ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
  ... on ProjectV2ItemFieldIterationValue { title field { ... on ProjectV2FieldCommon { name } } }
} }
"""

_CONTENT = """
content { __typename
  ... on Issue { id url title }
  ... on PullRequest { id url title }
  ... on DraftIssue { id title }
}
"""


def _walk(data: object, path: Sequence[str]) -> Any:
    for key in path:
        if not isinstance(data, dict) or data.get(key) is None:
            return None
        data = data[key]
    return data


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise GhProjectsError(f"{value!r} is not a number")
    return float(value)


def encode_value(target: Field, value: object) -> dict[str, object]:
    """The ProjectV2FieldValue input for `value`, resolved against the field's own options.

    Raising here, before any request, is the guard: GitHub answers an unknown option id with a generic
    failure that does not say which names were valid.
    """
    kind = target.data_type
    if kind == "TEXT":
        return {"text": str(value)}
    if kind == "NUMBER":
        return {"number": _as_float(value)}
    if kind == "DATE":
        return {"date": str(value)}
    if kind == "SINGLE_SELECT":
        if str(value) not in target.options:
            raise UnknownOption(
                f"{target.name!r} has no option {value!r}; valid: {sorted(target.options)}"
            )
        return {"singleSelectOptionId": target.options[str(value)]}
    if kind == "ITERATION":
        if str(value) not in target.iterations:
            raise UnknownOption(
                f"{target.name!r} has no iteration {value!r}; valid: {sorted(target.iterations)}"
            )
        return {"iterationId": target.iterations[str(value)]}
    raise GhProjectsError(
        f"{target.name!r} is a {kind} field; it is changed through the issue or pull request, "
        "not as a project field value"
    )


class Projects:
    def __init__(self, run: Runner = gh_runner) -> None:
        self._run = run
        self._scopes: tuple[str, ...] | None | bool = False  # False: not asked yet

    # -- transport ---------------------------------------------------------------------------------

    def graphql(
        self, query: str, variables: dict[str, object] | None = None
    ) -> dict[str, Any]:
        """One request. Raises on any error; use `graphql_partial` for batched writes."""
        data, errors = self.graphql_partial(query, variables)
        if errors:
            raise self._error(errors[0])
        return data

    def graphql_partial(
        self, query: str, variables: dict[str, object] | None = None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        body = json.dumps({"query": query, "variables": variables or {}})
        done = self._run(["api", "graphql", "--input", "-"], body)
        for pause in (2, 8):
            if done.code == 0 or not any(t in done.err for t in TRANSIENT):
                break
            # GitHub answers a heavy request with a bare 502 now and then, and the network drops a TLS
            # handshake (both seen on 2026-11-07, the second ending a 300-item board run). Two spaced
            # retries; a third failure is reported, not looped on.
            time.sleep(pause)
            done = self._run(["api", "graphql", "--input", "-"], body)
        try:
            payload = json.loads(done.out or "null")
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            # gh prints JSON on failure too; reaching here means it did not get as far as GitHub.
            raise GhProjectsError(
                f"no usable response (exit {done.code}): {done.err.strip()[:300]}"
            )
        errors = [e for e in payload.get("errors") or [] if isinstance(e, dict)]
        return payload.get("data") or {}, errors

    def _error(self, error: dict[str, Any]) -> GhProjectsError:
        kind = str(error.get("type", ""))
        message = str(error.get("message", ""))
        if kind == "INSUFFICIENT_SCOPES":
            return MissingScope("read:project", self.scopes() or ())
        if kind == "NOT_FOUND":
            return NotFound(message)
        if kind in ("RATE_LIMITED", "MAX_NODE_LIMIT_EXCEEDED"):
            return GhProjectsError(f"{kind}: {message}")
        return GhProjectsError(f"{kind or 'error'}: {message}")

    def scopes(self) -> tuple[str, ...] | None:
        """The token's granted scopes, or None when GitHub does not report any.

        Fine-grained and app tokens send no X-OAuth-Scopes header. None means "unknown", and `require`
        treats unknown as permitted so the API itself gets to decide.
        """
        if self._scopes is False:
            done = self._run(["api", "-i", "user"], None)
            found: tuple[str, ...] | None = None
            for line in done.out.splitlines():
                name, _, value = line.partition(":")
                if name.strip().lower() == "x-oauth-scopes":
                    found = tuple(s.strip() for s in value.split(",") if s.strip())
            self._scopes = found
        return self._scopes if isinstance(self._scopes, tuple) else None

    def require(self, write: bool = False) -> None:
        granted = self.scopes()
        if granted is None:
            return
        acceptable = WRITE_SCOPES if write else READ_SCOPES
        if not any(scope in granted for scope in acceptable):
            raise MissingScope(acceptable[-1] if write else READ_SCOPES[0], granted)

    def rate_limit(self) -> tuple[int, str]:
        data = self.graphql("{ rateLimit { remaining resetAt } }")
        limit = data["rateLimit"]
        return int(limit["remaining"]), str(limit["resetAt"])

    def guard(self, needed: int) -> None:
        remaining, reset_at = self.rate_limit()
        if remaining < needed:
            raise RateLimited(remaining, reset_at)

    def _pages(
        self, query: str, variables: dict[str, object], path: Sequence[str]
    ) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        """(whole response, the connection at `path`) per page, until hasNextPage is false."""
        after: str | None = None
        while True:
            data = self.graphql(query, {**variables, "after": after})
            connection = _walk(data, path)
            if connection is None:
                raise NotFound(f"nothing at {'.'.join(path)} for {variables}")
            yield data, connection
            info = connection["pageInfo"]
            if not info["hasNextPage"]:
                return
            after = info["endCursor"]

    # -- reads -------------------------------------------------------------------------------------

    def list_projects(self, owner: str) -> list[Project]:
        """Every project the owner has, closed ones included (gh project list hides them)."""
        self.require()
        query = (
            "query($owner: String!, $after: String) { repositoryOwner(login: $owner) { "
            "... on ProjectV2Owner { projectsV2(first: 100, after: $after) { "
            "pageInfo { hasNextPage endCursor } "
            "nodes { id number title closed url } } } } }"
        )
        out: list[Project] = []
        for _, connection in self._pages(
            query, {"owner": owner}, ("repositoryOwner", "projectsV2")
        ):
            out += [
                Project(
                    id=n["id"],
                    number=n["number"],
                    title=n["title"],
                    closed=n["closed"],
                    url=n["url"],
                )
                for n in connection["nodes"]
            ]
        return out

    def project(self, owner: str, number: int) -> Project:
        """A project with its fields, options and iterations resolved by name."""
        self.require()
        query = (
            "query($owner: String!, $number: Int!, $after: String) { repositoryOwner(login: $owner) { "
            "... on ProjectV2Owner { projectV2(number: $number) { id number title closed url "
            f"fields(first: 100, after: $after) {{ pageInfo {{ hasNextPage endCursor }} {_FIELDS} }} }} }} }} }}"
        )
        fields: dict[str, Field] = {}
        meta: dict[str, Any] | None = None
        for data, connection in self._pages(
            query,
            {"owner": owner, "number": number},
            ("repositoryOwner", "projectV2", "fields"),
        ):
            meta = meta or data["repositoryOwner"]["projectV2"]
            for node in connection["nodes"]:
                if not node:
                    continue
                fields[node["name"]] = Field(
                    id=node["id"],
                    name=node["name"],
                    data_type=node["dataType"],
                    options={o["name"]: o["id"] for o in node.get("options") or []},
                    iterations={
                        i["title"]: i["id"]
                        for i in (node.get("configuration") or {}).get("iterations", [])
                    },
                )
        assert meta is not None  # _pages raises NotFound before yielding nothing
        return Project(
            id=meta["id"],
            number=meta["number"],
            title=meta["title"],
            closed=meta["closed"],
            url=meta["url"],
            fields=fields,
        )

    def items(self, owner: str, number: int) -> list[Item]:
        """Every item with its field values keyed by field name. REDACTED items are kept, not dropped."""
        self.require()
        query = (
            "query($owner: String!, $number: Int!, $after: String) { repositoryOwner(login: $owner) { "
            "... on ProjectV2Owner { projectV2(number: $number) { "
            "items(first: 50, after: $after) { pageInfo { hasNextPage endCursor } "
            f"nodes {{ id type {_CONTENT} {_VALUES} }} }} }} }} }} }}"
        )
        out: list[Item] = []
        for _, connection in self._pages(
            query,
            {"owner": owner, "number": number},
            ("repositoryOwner", "projectV2", "items"),
        ):
            for node in connection["nodes"]:
                content = node.get("content") or {}
                out.append(
                    Item(
                        id=node["id"],
                        type=node["type"],
                        content_id=content.get("id", ""),
                        url=content.get("url", ""),
                        title=content.get("title", ""),
                        values=_values(node.get("fieldValues")),
                        truncated=bool(
                            ((node.get("fieldValues") or {}).get("pageInfo") or {}).get(
                                "hasNextPage"
                            )
                        ),
                    )
                )
        return out

    def linked_ids(self, issue_id: str, connection: str) -> set[str]:
        """Node ids on an issue's `subIssues` or `blockedBy` connection, every page of it.

        One page used to be read: past 100 sub-issues a link that exists read as missing, so a check run
        reported drift that was not there and an apply run re-sent the mutation.
        """
        if connection not in ("subIssues", "blockedBy"):
            raise GhProjectsError(f"{connection!r} is not an issue link connection")
        self.require()
        query = (
            "query($id: ID!, $after: String) { node(id: $id) { ... on Issue { "
            f"{connection}(first: 100, after: $after) {{ pageInfo {{ hasNextPage endCursor }} nodes {{ id }} }} }} }} }}"
        )
        found: set[str] = set()
        for _, page in self._pages(query, {"id": issue_id}, ("node", connection)):
            found |= {str(n["id"]) for n in page["nodes"] if n}
        return found

    def readme(self, project_id: str) -> tuple[str, str]:
        """(readme, shortDescription) of a project, "" for either when unset."""
        self.require()
        node = (
            self.graphql(
                "query($id: ID!) { node(id: $id) { ... on ProjectV2 { readme shortDescription } } }",
                {"id": project_id},
            ).get("node")
            or {}
        )
        return str(node.get("readme") or ""), str(node.get("shortDescription") or "")

    # -- writes ------------------------------------------------------------------------------------

    def create_project(self, owner: str, title: str) -> Project:
        """A new project titled `title`, refused when any project of the owner's, open or closed, has it.

        Two open boards titled "Protocol SDK portfolio" (#2 and #4) are what a create without this check
        produced: every later lookup by title could then land on the stale one.
        """
        self.require(write=True)
        clash = [p for p in self.list_projects(owner) if p.title == title]
        if clash:
            listed = ", ".join(
                f"#{p.number}{' (closed)' if p.closed else ''}" for p in clash
            )
            raise GhProjectsError(
                f"{owner} already has a project titled {title!r}: {listed}; "
                "use it by number instead of creating another"
            )
        data = self.graphql(
            "query($owner: String!) { repositoryOwner(login: $owner) { id } }",
            {"owner": owner},
        )
        owner_id = _walk(data, ("repositoryOwner", "id"))
        if not owner_id:
            raise NotFound(f"no owner {owner!r}")
        made = _walk(
            self.graphql(
                "mutation($i: CreateProjectV2Input!) { createProjectV2(input: $i) { "
                "projectV2 { id number title closed url } } }",
                {"i": {"ownerId": owner_id, "title": title}},
            ),
            ("createProjectV2", "projectV2"),
        )
        if not made:
            raise GhProjectsError(f"creating {title!r} returned no project")
        return Project(
            id=made["id"],
            number=made["number"],
            title=made["title"],
            closed=made["closed"],
            url=made["url"],
        )

    def add_sub_issue(self, parent_id: str, child_id: str) -> None:
        self.require(write=True)
        self.graphql(
            "mutation($p: ID!, $c: ID!) { addSubIssue(input: {issueId: $p, subIssueId: $c, replaceParent: true}) "
            "{ issue { id } } }",
            {"p": parent_id, "c": child_id},
        )

    def add_blocked_by(self, issue_id: str, blocker_id: str) -> None:
        self.require(write=True)
        self.graphql(
            "mutation($i: ID!, $b: ID!) { addBlockedBy(input: {issueId: $i, blockingIssueId: $b}) "
            "{ issue { id } } }",
            {"i": issue_id, "b": blocker_id},
        )

    def add_items(self, project_id: str, content_ids: Sequence[str]) -> dict[str, str]:
        """Add issues or PRs by node id. Idempotent: an existing item's id comes back. Maps content to item.

        One aliased mutation per 50, not one request per issue: twenty-two sequential calls once took long
        enough to trip a command timeout, and an interrupted loop is a half-populated board.
        """
        self.require(write=True)
        added: dict[str, str] = {}
        for start in range(0, len(content_ids), BATCH):
            chunk = list(content_ids[start : start + BATCH])
            decls = ", ".join(f"$c{i}: ID!" for i in range(len(chunk)))
            body = " ".join(
                f"a{i}: addProjectV2ItemById(input: {{projectId: $p, contentId: $c{i}}}) {{ item {{ id }} }}"
                for i in range(len(chunk))
            )
            variables: dict[str, object] = {"p": project_id}
            variables.update({f"c{i}": cid for i, cid in enumerate(chunk)})
            data, errors = self.graphql_partial(
                f"mutation($p: ID!, {decls}) {{ {body} }}", variables
            )
            failed = {str(e["path"][0]) for e in errors if e.get("path")}
            if errors and not failed:
                raise self._error(errors[0])
            for i, cid in enumerate(chunk):
                node = (data.get(f"a{i}") or {}).get("item")
                if node:
                    added[cid] = node["id"]
        return added

    def set_values(self, project: Project, changes: Sequence[Change]) -> list[Outcome]:
        """Apply field writes in batches of FIELD_BATCH. Names are resolved first, so a bad one writes nothing."""
        self.require(write=True)
        resolved: list[tuple[Change, Field, dict[str, object] | None]] = []
        for change in changes:
            if change.field_name not in project.fields:
                raise UnknownOption(
                    f"project has no field {change.field_name!r}; valid: {sorted(project.fields)}"
                )
            target = project.fields[change.field_name]
            payload = (
                None if change.value is None else encode_value(target, change.value)
            )
            resolved.append((change, target, payload))
        outcomes: list[Outcome] = []
        for start in range(0, len(resolved), FIELD_BATCH):
            chunk = resolved[start : start + FIELD_BATCH]
            decls = ["$p: ID!"]
            parts: list[str] = []
            variables: dict[str, object] = {"p": project.id}
            for i, (change, target, payload) in enumerate(chunk):
                variables[f"i{i}"] = change.item_id
                variables[f"f{i}"] = target.id
                decls += [f"$i{i}: ID!", f"$f{i}: ID!"]
                if payload is None:
                    parts.append(
                        f"m{i}: clearProjectV2ItemFieldValue(input: {{projectId: $p, itemId: $i{i}, "
                        f"fieldId: $f{i}}}) {{ clientMutationId }}"
                    )
                else:
                    variables[f"v{i}"] = payload
                    decls.append(f"$v{i}: ProjectV2FieldValue!")
                    parts.append(
                        f"m{i}: updateProjectV2ItemFieldValue(input: {{projectId: $p, itemId: $i{i}, "
                        f"fieldId: $f{i}, value: $v{i}}}) {{ clientMutationId }}"
                    )
            _, errors = self.graphql_partial(
                f"mutation({', '.join(decls)}) {{ {' '.join(parts)} }}", variables
            )
            by_alias = {
                str(e["path"][0]): str(e.get("message", "failed"))
                for e in errors
                if e.get("path")
            }
            if errors and not by_alias:
                raise self._error(errors[0])
            outcomes += [
                Outcome(change, f"m{i}" not in by_alias, by_alias.get(f"m{i}", ""))
                for i, (change, _, _) in enumerate(chunk)
            ]
        return outcomes

    # -- schema and layout -------------------------------------------------------------------------

    def create_field(
        self,
        project_id: str,
        name: str,
        data_type: str,
        options: Sequence[dict[str, str]] = (),
        iteration: dict[str, object] | None = None,
    ) -> str:
        """Create a custom field. Returns its node id. data_type: TEXT, NUMBER, DATE, SINGLE_SELECT, ITERATION."""
        self.require(write=True)
        payload: dict[str, object] = {
            "projectId": project_id,
            "dataType": data_type,
            "name": name,
        }
        if options:
            payload["singleSelectOptions"] = list(options)
        if iteration:
            payload["iterationConfiguration"] = iteration
        data = self.graphql(
            "mutation($i: CreateProjectV2FieldInput!) { createProjectV2Field(input: $i) { "
            "projectV2Field { ... on ProjectV2FieldCommon { id } } } }",
            {"i": payload},
        )
        return str(data["createProjectV2Field"]["projectV2Field"]["id"])

    def set_options(self, field_id: str, options: Sequence[dict[str, str]]) -> None:
        """Replace a single-select field's options. Pass an option's `id` to keep it, and with it every
        item's value; an option sent without an id is new, and one left out is deleted."""
        self.require(write=True)
        self.graphql(
            "mutation($i: UpdateProjectV2FieldInput!) { updateProjectV2Field(input: $i) { "
            "projectV2Field { ... on ProjectV2FieldCommon { id } } } }",
            {"i": {"fieldId": field_id, "singleSelectOptions": list(options)}},
        )

    def describe(self, project_id: str, short: str, readme: str) -> None:
        self.require(write=True)
        self.graphql(
            "mutation($i: UpdateProjectV2Input!) { updateProjectV2(input: $i) { projectV2 { id } } }",
            {
                "i": {
                    "projectId": project_id,
                    "shortDescription": short,
                    "readme": readme,
                }
            },
        )

    def view_names(self, owner: str, number: int) -> list[str]:
        self.require()
        query = (
            "query($owner: String!, $number: Int!, $after: String) { repositoryOwner(login: $owner) { "
            "... on ProjectV2Owner { projectV2(number: $number) { views(first: 100, after: $after) { "
            "pageInfo { hasNextPage endCursor } nodes { name } } } } } }"
        )
        names: list[str] = []
        for _, connection in self._pages(
            query,
            {"owner": owner, "number": number},
            ("repositoryOwner", "projectV2", "views"),
        ):
            names += [n["name"] for n in connection["nodes"]]
        return names

    def rest_field_ids(self, owner: str, number: int) -> dict[str, int]:
        """Field name -> the numeric id the REST views endpoint wants (GraphQL node ids are not accepted)."""
        self.require()
        done = self._run(
            ["api", f"users/{owner}/projectsV2/{number}/fields?per_page=100"], None
        )
        try:
            listed = json.loads(done.out or "null")
        except json.JSONDecodeError:
            listed = None
        if not isinstance(listed, list):
            raise GhProjectsError(
                f"could not list fields over REST: {done.err.strip()[:200]}"
            )
        return {str(f["name"]): int(f["id"]) for f in listed}

    def create_view(self, owner: str, number: int, spec: dict[str, object]) -> None:
        """POST a view. There is no REST endpoint to update or delete one, so callers check by name first.

        The path takes the owner's login. The reference documents a numeric user id there, and that form
        returned 404 against the live API on 2026-11-07 while the login form created the view.
        """
        self.require(write=True)
        done = self._run(
            [
                "api",
                "-X",
                "POST",
                f"users/{owner}/projectsV2/{number}/views",
                "-H",
                "X-GitHub-Api-Version: 2026-03-10",
                "--input",
                "-",
            ],
            json.dumps(spec),
        )
        if done.code != 0:
            raise GhProjectsError(
                f"view {spec.get('name')!r} refused: {(done.out or done.err).strip()[:300]}"
            )


def _values(block: object) -> dict[str, object]:
    out: dict[str, object] = {}
    for node in (block or {}).get("nodes", []) if isinstance(block, dict) else []:
        if not node:
            continue
        name = (node.get("field") or {}).get("name")
        kind = node.get("__typename", "")
        if not name:
            continue
        if kind.endswith("TextValue"):
            out[name] = node["text"]
        elif kind.endswith("NumberValue"):
            out[name] = node["number"]
        elif kind.endswith("DateValue"):
            out[name] = node["date"]
        elif kind.endswith("SingleSelectValue"):
            out[name] = node["name"]
        elif kind.endswith("IterationValue"):
            out[name] = node["title"]
    return out
