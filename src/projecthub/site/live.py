"""The site's data (schema 3), built from the organization Project alone.

labs.b1tninja.com shows what Project 1 says, within minutes of it changing. One paginated GraphQL read of the
Project's items, with their field values and the few issue fields the page needs, is the whole input: no
product repository is checked out, no ROADMAP is read and no request is made per repository.

- The active set is the project cards whose Status is In progress, most recent activity first, capped at
  `--active` (default 6). Each gets its rows grouped by Status, counts, a progress figure, the current
  claims and its last ten activity events.
- Every other project is one line: name, Status and row counts.
- Activity is public, so it carries its kind and time only, never note text (the owner's decision,
  2026-11-08), and the Project is not linked while it is private.
- Each build records its request count and GraphQL cost, and refuses to run past a point budget.

    projecthub site --out data
    projecthub site --out data --active 6 --budget 100

The contract is docs/DATA.md here, copied to DATA.md in the site repository. A file that would break it is
refused before anything is written.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from projecthub import config

SCHEMA_VERSION = 3
DEFAULT_ACTIVE = 6
DEFAULT_BUDGET = 100
ACTIVITY_LIMIT = 10
PAGE_SIZE = 100
ACTIVE_STATUS = "In progress"
DONE_STATUS = "Done"
NO_STATUS = "No status"
# The Status options in the Project's own order (github/board.py STATUS). An option added later sorts after.
STATUS_ORDER = (
    "Triage",
    "Backlog",
    "Ready",
    "In progress",
    "In review",
    "Blocked",
    "Done",
)
# A row someone is working: its Agent is shown as the holder. A released or done row keeps no holder.
HOLDING = ("In progress", "In review", "Blocked")
NONGOAL_LABEL = "type:non-goal"
NAME = re.compile(r"^[A-Za-z0-9._-]+$")
# A row card is `[project] row(): summary`; the same shape mcp/loop.py's CARD reads.
ROW = re.compile(
    r"^\[(?P<project>[^\]]+)\]\s+(?P<row>[\w.@/\[\]()+-]+)\s*(?::\s*(?P<summary>.*))?$"
)
# A project card is `name: what` (github/board.py project_title); waves and decisions have their own prefix.
PROJECT_TITLE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+):\s*(?P<what>.*)$")
NOT_PROJECTS = re.compile(r"^(Wave|Decision) \d+:")
# "Last activity" is written as `<kind> <ISO time>` (mcp/work.py). Anything else is not published.
LAST_ACTIVITY = re.compile(
    r"^(?P<kind>[a-z][a-z_:-]{0,39}) (?P<at>\d{4}-\d\d-\d\d(?:T\d\d:\d\d(?::\d\d)?Z)?)$"
)
DATE = re.compile(r"^\d{4}-\d\d-\d\d$")
AGENT = re.compile(r"[^A-Za-z0-9._@-]")
URL_BAD = re.compile(r"[\s\"'<>]")

QUERY = """
query($owner: String!, $number: Int!, $first: Int!, $after: String) {
  rateLimit { cost remaining resetAt }
  organization(login: $owner) { projectV2(number: $number) { public
    items(first: $first, after: $after) { totalCount pageInfo { hasNextPage endCursor }
      nodes {
        fieldValues(first: 30) { nodes { __typename
          ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
          ... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
          ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
        } }
        content { __typename ... on Issue { title url state updatedAt labels(first: 10) { nodes { name } } } }
      } } } } }
"""


class GraphQL(Protocol):
    """The one method the site needs: `projecthub.github.client.Projects` has it, and so do the tests' fakes."""

    def graphql(
        self, query: str, variables: dict[str, object] | None = None
    ) -> dict[str, Any]: ...


class OverBudget(RuntimeError):
    pass


@dataclass(frozen=True)
class Card:
    title: str
    url: str
    state: str
    updated: str
    labels: tuple[str, ...]
    values: dict[str, str]


@dataclass
class Read:
    """What one build read: the cards, whether the Project is public, and what reading it cost."""

    cards: list[Card]
    public: bool
    total: int = 0
    requests: int = 0
    cost: int = 0
    remaining: int | None = None
    budget: int = DEFAULT_BUDGET

    def meta(self) -> dict[str, Any]:
        return {
            "items": self.total,
            "requests": self.requests,
            "cost": self.cost,
            "budget": self.budget,
            "remaining": self.remaining,
        }


# -- reading the Project ------------------------------------------------------------------------------------


def _values(node: dict[str, Any]) -> dict[str, str]:
    out = {}
    for value in (node.get("fieldValues") or {}).get("nodes") or []:
        name = ((value or {}).get("field") or {}).get("name")
        if not name:
            continue
        found = value.get("text", value.get("name", value.get("date")))
        if found is not None:
            out[str(name)] = str(found)
    return out


def _card(node: dict[str, Any]) -> Card | None:
    content = node.get("content") or {}
    if content.get("__typename") != "Issue":
        return None
    return Card(
        title=str(content.get("title") or "").strip(),
        url=str(content.get("url") or ""),
        state=str(content.get("state") or ""),
        updated=str(content.get("updatedAt") or ""),
        labels=tuple(
            str(n.get("name"))
            for n in (content.get("labels") or {}).get("nodes") or []
            if n
        ),
        values=_values(node),
    )


def read(
    client: GraphQL,
    owner: str = config.PROJECT_OWNER,
    number: int = config.PROJECT_NUMBER,
    budget: int = DEFAULT_BUDGET,
) -> Read:
    """Every item of the Project, page by page, refusing before a page that would take the run past `budget`.

    The first page states the item count and its own cost, so the whole run is estimated before a second
    request is made; the running total is checked again after every page.
    """
    out = Read(cards=[], public=False, budget=budget)
    after: str | None = None
    while True:
        data = client.graphql(
            QUERY,
            {"owner": owner, "number": number, "first": PAGE_SIZE, "after": after},
        )
        out.requests += 1
        limit = data.get("rateLimit") or {}
        out.cost += int(limit.get("cost") or 1)
        if limit.get("remaining") is not None:
            out.remaining = int(limit["remaining"])
        project = (data.get("organization") or {}).get("projectV2")
        if project is None:
            raise RuntimeError(f"no Project {owner}/{number} visible to this token")
        out.public = bool(project.get("public"))
        items = project["items"]
        out.total = int(items.get("totalCount") or 0)
        if out.requests == 1:
            pages = max(1, math.ceil(out.total / PAGE_SIZE))
            estimate = out.cost * pages
            if estimate > budget:
                raise OverBudget(
                    f"{out.total} items need {pages} requests, about {estimate} points; the budget is {budget}"
                )
        if out.cost > budget:
            raise OverBudget(
                f"{out.cost} points spent after {out.requests} requests; the budget is {budget}"
            )
        out.cards.extend(c for c in map(_card, items.get("nodes") or []) if c)
        if not items["pageInfo"]["hasNextPage"]:
            return out
        after = items["pageInfo"]["endCursor"]


# -- shaping it -----------------------------------------------------------------------------------------------


def level(card: Card) -> str:
    """Project, Row or "" for anything else. The Level field decides when it is set; the title otherwise."""
    lvl = card.values.get("Level", "")
    if lvl == "Project":
        return "Project" if PROJECT_TITLE.match(card.title) else ""
    if lvl == "Obligation":
        return "Row" if ROW.match(card.title) else ""
    if lvl:
        return ""
    if ROW.match(card.title):
        return "Row"
    if PROJECT_TITLE.match(card.title) and not NOT_PROJECTS.match(card.title):
        return "Project"
    return ""


def status_of(card: Card) -> str:
    return card.values.get("Status") or NO_STATUS


def _status_key(status: str) -> tuple[int, str]:
    return (
        STATUS_ORDER.index(status) if status in STATUS_ORDER else len(STATUS_ORDER),
        status,
    )


def event(card: Card) -> dict[str, str] | None:
    """The card's latest event as kind and time, or None. Text that is not `<kind> <time>` is never published."""
    match = LAST_ACTIVITY.match(card.values.get("Last activity", "").strip())
    if match:
        return {"kind": match.group("kind"), "at": match.group("at")}
    claimed = card.values.get("Claimed", "").strip()
    if DATE.match(claimed):
        return {"kind": "claim", "at": claimed}
    return None


def agent(card: Card) -> str:
    return AGENT.sub("", card.values.get("Agent", ""))[:64]


def https(url: str) -> str | None:
    return url if url.startswith("https://") and not URL_BAD.search(url) else None


def issues_url(name: str) -> str:
    """The project's issues in the planning repository: every row title starts with `[name]`."""
    return f"https://github.com/{config.PORTFOLIO_REPO}/issues?q=is%3Aissue+in%3Atitle+%22%5B{name}%5D%22"


def board_url(public: bool) -> str:
    """The Project when anyone can open it; the planning repository's issues while it is private."""
    return (
        config.BOARD_URL
        if public
        else f"https://github.com/{config.PORTFOLIO_REPO}/issues"
    )


def project_is_public(found: Read) -> bool:
    """Read from the same request as the items, so the check costs nothing extra."""
    return found.public


def counts(rows: Iterable[Card]) -> dict[str, int]:
    out: dict[str, int] = {}
    for card in rows:
        out[status_of(card)] = out.get(status_of(card), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: _status_key(kv[0])))


def _row(card: Card) -> dict[str, Any]:
    match = ROW.match(card.title)
    assert match is not None
    held = agent(card) if status_of(card) in HOLDING else ""
    claimed = card.values.get("Claimed", "")
    return {
        "id": match.group("row").removesuffix("()"),
        "title": (match.group("summary") or "").replace("`", "").strip(),
        "url": https(card.url),
        "holder": held or None,
        "since": claimed if held and DATE.match(claimed) else None,
    }


def latest(events: Iterable[dict[str, str] | None]) -> list[dict[str, str]]:
    return sorted((e for e in events if e), key=lambda e: e["at"], reverse=True)


@dataclass
class Project:
    name: str
    card: Card | None = None
    rows: list[Card] = field(default_factory=list)

    @property
    def status(self) -> str | None:
        return status_of(self.card) if self.card else None

    @property
    def work(self) -> list[Card]:
        return [r for r in self.rows if NONGOAL_LABEL not in r.labels]

    def last_activity(self) -> str | None:
        found = latest(
            event(c) for c in [*self.rows, *([self.card] if self.card else [])]
        )
        return found[0]["at"] if found else None

    def progress(self) -> dict[str, int]:
        work = self.work
        return {
            "done": sum(1 for r in work if status_of(r) == DONE_STATUS),
            "total": len(work),
        }


def projects(cards: Iterable[Card]) -> dict[str, Project]:
    """Every project the Project knows of, by name: its card when it has one, its rows in title order."""
    out: dict[str, Project] = {}
    for card in cards:
        kind = level(card)
        if kind == "Project":
            match = PROJECT_TITLE.match(card.title)
            assert match is not None
            name = match.group("name")
            out.setdefault(name, Project(name)).card = card
        elif kind == "Row":
            match = ROW.match(card.title)
            assert match is not None
            name = match.group("project").strip()
            if NAME.match(name):
                out.setdefault(name, Project(name)).rows.append(card)
    return out


def active(found: dict[str, Project], limit: int) -> list[Project]:
    """Projects whose card is In progress, most recent activity first (none last, then by name), at most `limit`."""
    chosen = [p for p in found.values() if p.status == ACTIVE_STATUS]
    chosen.sort(key=lambda p: p.name)
    chosen.sort(key=lambda p: p.last_activity() or "", reverse=True)
    return chosen[: max(0, limit)]


def active_entry(p: Project) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for card in sorted(p.work, key=lambda c: _status_key(status_of(c))):
        groups.setdefault(status_of(card), []).append(_row(card))
    what = PROJECT_TITLE.match(p.card.title) if p.card else None
    rows = [_row(c) for c in p.work]
    return {
        "name": p.name,
        "what": what.group("what").strip() if what else "",
        "status": p.status,
        "url": https(p.card.url) if p.card else None,
        "issues_url": issues_url(p.name),
        "last_activity": p.last_activity(),
        "counts": counts(p.work),
        "nongoals": len(p.rows) - len(p.work),
        "progress": p.progress(),
        "groups": [{"status": s, "rows": r} for s, r in groups.items()],
        "claims": [
            {"row": r["id"], "holder": r["holder"], "since": r["since"]}
            for r in rows
            if r["holder"]
        ],
        "activity": latest(event(c) for c in [*p.rows, *([p.card] if p.card else [])])[
            :ACTIVITY_LIMIT
        ],
    }


def other_entry(p: Project) -> dict[str, Any]:
    return {
        "name": p.name,
        "status": p.status,
        "issues_url": issues_url(p.name),
        "counts": counts(p.work),
        "progress": p.progress(),
    }


def build(
    found: Read, limit: int = DEFAULT_ACTIVE, now: datetime | None = None
) -> dict[str, Any]:
    """index.json from one read of the Project."""
    every = projects(found.cards)
    chosen = active(every, limit)
    picked = {p.name for p in chosen}
    rest = sorted(
        (p for p in every.values() if p.name not in picked),
        key=lambda p: (_status_key(p.status or NO_STATUS), p.name),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "board_url": board_url(project_is_public(found)),
        "issues_url": f"https://github.com/{config.PORTFOLIO_REPO}/issues",
        "active_limit": limit,
        "build": found.meta(),
        "active": [active_entry(p) for p in chosen],
        "others": [other_entry(p) for p in rest],
    }


# -- the contract -----------------------------------------------------------------------------------------------

INDEX_KEYS = {
    "schema_version",
    "generated_at",
    "board_url",
    "issues_url",
    "active_limit",
    "build",
    "active",
    "others",
}
ACTIVE_KEYS = {
    "name",
    "what",
    "status",
    "url",
    "issues_url",
    "last_activity",
    "counts",
    "nongoals",
    "progress",
    "groups",
    "claims",
    "activity",
}
OTHER_KEYS = {"name", "status", "issues_url", "counts", "progress"}
ROW_KEYS = {"id", "title", "url", "holder", "since"}


def problems(index: dict[str, Any], public: bool) -> list[str]:
    """The rules DATA.md says the publisher enforces."""
    found = []
    if set(index) != INDEX_KEYS:
        found.append(
            f"index keys {sorted(set(index) ^ INDEX_KEYS)} differ from the contract"
        )
    if index.get("schema_version") != SCHEMA_VERSION:
        found.append("schema_version is not 3")
    if not public and str(index.get("board_url", "")).startswith(config.BOARD_URL):
        found.append("the Project is private but board_url links it")
    names = [p["name"] for p in [*index.get("active", []), *index.get("others", [])]]
    if len(set(names)) != len(names):
        found.append("duplicate project names")
    urls = [index.get("board_url"), index.get("issues_url")]
    for p in index.get("active", []):
        if set(p) != ACTIVE_KEYS:
            found.append(f"{p.get('name')}: keys differ from the contract")
        if len(p.get("activity", [])) > ACTIVITY_LIMIT:
            found.append(f"{p['name']}: more than {ACTIVITY_LIMIT} activity events")
        for entry in p.get("activity", []):
            if set(entry) != {"at", "kind"} or not LAST_ACTIVITY.match(
                f"{entry.get('kind')} {entry.get('at')}"
            ):
                found.append(f"{p['name']}: activity carries more than kind and time")
        for group in p.get("groups", []):
            for row in group["rows"]:
                if set(row) != ROW_KEYS:
                    found.append(f"{p['name']}: row {row.get('id')} keys differ")
                urls.append(row.get("url"))
        urls += [p.get("url"), p.get("issues_url")]
    for p in index.get("others", []):
        if set(p) != OTHER_KEYS:
            found.append(f"{p.get('name')}: keys differ from the contract")
        urls.append(p.get("issues_url"))
    for name in names:
        if not NAME.match(str(name)):
            found.append(f"{name!r} is not a valid name")
    for url in urls:
        if url is not None and https(str(url)) is None:
            found.append(f"{url!r} is not a plain https:// URL")
        if url is not None and "/projects/" in str(url) and not public:
            found.append(f"{url!r} links the private Project")
    return found


def write(out: pathlib.Path, index: dict[str, Any]) -> None:
    """index.json, and nothing else: schema 2's per-project files are removed."""
    out.mkdir(parents=True, exist_ok=True)
    stale = out / "p"
    if stale.is_dir():
        for old in stale.glob("*.json"):
            old.unlink()
        if not any(stale.iterdir()):
            stale.rmdir()
    (out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def _client() -> GraphQL:
    from projecthub.github.client import Projects

    return Projects()


def main(argv: Sequence[str] | None = None, client: GraphQL | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub site", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument(
        "--active",
        type=int,
        default=DEFAULT_ACTIVE,
        help=f"how many In progress projects get a full card (default {DEFAULT_ACTIVE})",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_BUDGET,
        help=f"GraphQL points one build may spend (default {DEFAULT_BUDGET})",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        found = read(client or _client(), budget=args.budget)
    except OverBudget as exc:
        print(f"error: over budget: {exc}", file=sys.stderr)
        return 1
    index = build(found, args.active)
    bad = problems(index, found.public)
    if bad:
        print(
            "error: the site data breaks its contract:\n  " + "\n  ".join(bad),
            file=sys.stderr,
        )
        return 1
    write(args.out, index)
    print(
        f"wrote {args.out}: {len(index['active'])} active and {len(index['others'])} other projects "
        f"from {found.total} items in {found.requests} requests costing {found.cost} points"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
