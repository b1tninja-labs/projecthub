"""Jira-style reads over the portfolio: filter rows across projects, draw the dependency graph, summarise the plan.

Three pure views, each over the same sources the rest of projecthub reads and none of them a copy:

- `query` filters obligation rows across every project's matrix (db/parser.py) by project, state, milestone,
  wave, tier, kind, named-test state, lease claim and lease block, spec text and id/title text.
- `graph` is the registry's `depends` arrows as edges, a topological order, and every cycle, reported rather
  than broken: a cycle is a planning error a person must resolve, and an order that silently dropped one would
  hide it.
- `plan` is one answer to "what now": the ready rows (`read.ready_rows`, unchanged), what is blocked and on
  what, the live work in progress against the two-active-projects rule, and the open rows that have gone
  longest unchanged.

Nothing here writes. The lease store is opened read-only (`mode=ro`), and only when its file already exists:
`work.Store()` would create the database and expire stale leases as a side effect of reading, which is right
for the lease server and wrong for a read tool. Results are deterministic and say `truncated` when a limit cut
them, never shortening a list without saying so.

Usage:
    projecthub query [--project P] [--state open] [--claimed false] [--text anchor] [--json]
    projecthub graph [--project P] [--mermaid] [--json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sqlite3
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypedDict

from projecthub import paths, registry
from projecthub.db import parser as roadmap

STATES = ("open", "part", "done", "nongoal")
TEST_STATES = ("exists", "to be written", "missing", "none")
FILTERS = (
    "project",
    "state",
    "milestone",
    "wave",
    "tier",
    "kind",
    "has_test",
    "claimed",
    "blocked",
    "spec_contains",
    "text",
)
MAX_LIMIT = 1000
ACTIVE_LIMIT = (
    2  # PORTFOLIO.md section 7: at most two projects carry real effort at once
)
UNFINISHED = ("open", "part")


class QueryError(ValueError):
    """A request the tools refuse: an unknown filter, a bad value, a project the registry does not hold."""


def default_root() -> pathlib.Path:
    """The workspace: PROJECTHUB_ROOT, then this machine's layout."""
    return pathlib.Path(paths.env("ROOT", "D:/code"))


# -- leases, read-only ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Leases:
    """Live claims (row key -> holder, expiry) and rows whose latest lease ended blocked (row key -> note)."""

    live: dict[str, dict[str, str]] = field(default_factory=dict)
    blocked: dict[str, str] = field(default_factory=dict)


def lease_db_path() -> pathlib.Path:
    """Where work.Store keeps its database: PROJECTHUB_LEASES_DB, then its default."""
    from projecthub.mcp import work

    named = paths.env("LEASES_DB")
    return pathlib.Path(named) if named else work.DEFAULT_DB


def read_leases(path: pathlib.Path | None = None, now: float | None = None) -> Leases:
    """The lease store's live and blocked rows, without writing to it.

    The same rules as work.Store.live_keys and blocked_keys: live means state 'live' and not yet past its
    expiry (Store would mark the rest expired on its next write), blocked means the row's latest lease ended
    blocked. No database file means no leases, not an error.
    """
    from projecthub.mcp import work

    target = path or lease_db_path()
    if not target.is_file():
        return Leases()
    stamp = time.time() if now is None else now
    db = sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        live = db.execute(
            "SELECT row_key, holder, expires_at FROM leases WHERE state='live' AND expires_at > ? "
            "ORDER BY row_key",
            (stamp,),
        ).fetchall()
        blocked = db.execute(
            "SELECT row_key, note FROM leases AS l WHERE state='blocked' AND acquired_at="
            "(SELECT MAX(acquired_at) FROM leases WHERE row_key=l.row_key) ORDER BY row_key"
        ).fetchall()
    except sqlite3.OperationalError:  # a file with no lease tables yet
        return Leases()
    finally:
        db.close()
    return Leases(
        {
            r["row_key"]: {
                "holder": r["holder"],
                "expires_at": work._iso(r["expires_at"]),
            }
            for r in live
        },
        {r["row_key"]: r["note"] or "" for r in blocked},
    )


# -- query ------------------------------------------------------------------------------------------


class ClaimOut(TypedDict):
    holder: str
    expires_at: str


class QueryRow(TypedDict):
    project: str
    row_id: str
    title: str
    state: str
    status: str
    milestone: str
    spec: str
    named_test: str
    test_state: str
    kind: str
    tier: int
    wave: int
    claimed: bool
    claim: ClaimOut | None
    blocked: bool
    block: str


class QueryOut(TypedDict):
    rows: list[QueryRow]
    total: int
    truncated: bool


def _bool(name: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false", "yes", "no"):
        return value.lower() in ("true", "yes")
    raise QueryError(f"{name} must be true or false, not {value!r}")


def _int(name: str, value: object) -> int:
    if isinstance(value, bool):
        raise QueryError(f"{name} must be a number, not {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    raise QueryError(f"{name} must be a number, not {value!r}")


def normalise(filters: Mapping[str, object]) -> dict[str, object]:
    """Filters checked and put in one form. Empty values (None, "") mean "no filter"; unknown keys refuse."""
    unknown = sorted(set(filters) - set(FILTERS))
    if unknown:
        raise QueryError(
            f"unknown filter {', '.join(unknown)}; the filters are {', '.join(FILTERS)}"
        )
    out: dict[str, object] = {}
    for key, value in filters.items():
        if value is None or value == "":
            continue
        if key == "state":
            text = str(value).lower()
            if text not in STATES:
                raise QueryError(f"state must be one of {', '.join(STATES)}")
            out[key] = text
        elif key == "has_test":
            text = str(value).lower().replace("_", " ")
            if text not in TEST_STATES:
                raise QueryError(f"has_test must be one of {', '.join(TEST_STATES)}")
            out[key] = text
        elif key in ("claimed", "blocked"):
            out[key] = _bool(key, value)
        elif key in ("wave", "tier"):
            out[key] = _int(key, value)
        else:
            out[key] = str(value)
    return out


def _entries(
    entries: Sequence[registry.Entry] | None,
) -> list[registry.Entry]:
    return list(registry.entries() if entries is None else entries)


def _rank(entry: registry.Entry) -> tuple[int, int, str]:
    """Wave, then tier, then name. Wave and tier 0 mean unsequenced and untiered, so they sort last."""
    return (entry.wave or 99, entry.tier or 99, entry.name)


def query(
    root: pathlib.Path,
    filters: Mapping[str, object],
    limit: int = 50,
    *,
    leases: Leases | None = None,
    entries: Sequence[registry.Entry] | None = None,
) -> QueryOut:
    """Rows across projects matching every filter, sorted by wave, tier, project and matrix order."""
    wanted = normalise(filters)
    everyone = _entries(entries)
    known = {e.name for e in everyone}
    project = str(wanted.get("project", ""))
    if project and project not in known:
        raise QueryError(f"no project {project!r} in the registry")
    held = read_leases() if leases is None else leases
    needle = str(wanted.get("text", "")).lower()
    spec_needle = str(wanted.get("spec_contains", "")).lower()
    milestone = str(wanted.get("milestone", "")).lower()
    kind = str(wanted.get("kind", "")).lower()
    found: list[tuple[tuple[int, int, str, int], QueryRow]] = []
    for entry in sorted(everyone, key=_rank):
        if project and entry.name != project:
            continue
        if "wave" in wanted and entry.wave != wanted["wave"]:
            continue
        if "tier" in wanted and entry.tier != wanted["tier"]:
            continue
        if kind and entry.kind.lower() != kind:
            continue
        path = roadmap.matrix_path(root, entry.name)
        if path is None:
            continue
        for row in roadmap.rows_of(path.read_text(encoding="utf-8")):
            if "state" in wanted and row["state"] != wanted["state"]:
                continue
            if milestone and row["milestone"].lower() != milestone:
                continue
            if spec_needle and spec_needle not in row["spec"].lower():
                continue
            if needle and not (
                needle in row["name"].lower() or needle in row["title"].lower()
            ):
                continue
            key = f"{entry.name}:{row['name']}"
            claim = held.live.get(key)
            if "claimed" in wanted and (claim is not None) != wanted["claimed"]:
                continue
            is_blocked = key in held.blocked
            if "blocked" in wanted and is_blocked != wanted["blocked"]:
                continue
            node, test_state = roadmap.named_test(
                root, entry.name, row["where"], row["evidence"]
            )
            if "has_test" in wanted and test_state != wanted["has_test"]:
                continue
            found.append(
                (
                    (*_rank(entry), int(row.get("line") or 0)),
                    {
                        "project": entry.name,
                        "row_id": row["name"],
                        "title": row["title"],
                        "state": row["state"],
                        "status": row["status"],
                        "milestone": row["milestone"],
                        "spec": row["spec"],
                        "named_test": node,
                        "test_state": test_state,
                        "kind": entry.kind,
                        "tier": entry.tier,
                        "wave": entry.wave,
                        "claimed": claim is not None,
                        "claim": (
                            {
                                "holder": claim["holder"],
                                "expires_at": claim["expires_at"],
                            }
                            if claim
                            else None
                        ),
                        "blocked": is_blocked,
                        "block": held.blocked.get(key, ""),
                    },
                )
            )
    found.sort(key=lambda pair: pair[0])
    cap = max(1, min(int(limit), MAX_LIMIT))
    return {
        "rows": [row for _, row in found[:cap]],
        "total": len(found),
        "truncated": len(found) > cap,
    }


# -- graph ------------------------------------------------------------------------------------------


class GraphNode(TypedDict):
    project: str
    kind: str
    tier: int
    wave: int
    depends: list[str]
    open_rows: int
    in_cycle: bool


class GraphEdge(TypedDict):
    project: str
    depends_on: str


class GraphOut(TypedDict):
    project: str
    format: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    order: list[str]
    unordered: list[str]
    cycles: list[list[str]]
    unknown: list[str]
    mermaid: str


def open_rows(root: pathlib.Path, project: str) -> int:
    """Rows still to do: state open or part. Done rows and declared non-goals are not open work."""
    path = roadmap.matrix_path(root, project)
    if path is None:
        return 0
    rows = roadmap.rows_of(path.read_text(encoding="utf-8"))
    return sum(1 for r in rows if r["state"] in UNFINISHED)


def _cycles(names: list[str], depends: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components with more than one member, or a project that depends on itself.

    Tarjan's algorithm, iterative so a long chain cannot hit the recursion limit. Each cycle lists its members
    sorted, and the cycles are sorted by their first member.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    counter = 0
    for start in names:
        if start in index:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, at = work.pop()
            if at == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            targets = [d for d in depends.get(node, []) if d in depends]
            if at < len(targets):
                work.append((node, at + 1))
                nxt = targets[at]
                if nxt not in index:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            if low[node] == index[node]:
                members: list[str] = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    members.append(top)
                    if top == node:
                        break
                if len(members) > 1 or node in depends.get(node, []):
                    found.append(sorted(members))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return sorted(found)


def _mermaid_id(name: str) -> str:
    return re.sub(r"\W", "_", name)


def mermaid(nodes: list[GraphNode], edges: list[GraphEdge]) -> str:
    """`graph TD` with each dependency above what needs it: an arrow reads "is needed by"."""
    lines = ["graph TD"]
    for node in nodes:
        label = f"{node['project']} ({node['open_rows']} open)"
        lines.append(f'    {_mermaid_id(node["project"])}["{label}"]')
    for edge in edges:
        lines.append(
            f"    {_mermaid_id(edge['depends_on'])} --> {_mermaid_id(edge['project'])}"
        )
    looped = [n["project"] for n in nodes if n["in_cycle"]]
    if looped:
        lines.append("    classDef cycle stroke:#d00,stroke-width:3px")
        lines.append(f"    class {','.join(_mermaid_id(n) for n in looped)} cycle")
    return "\n".join(lines) + "\n"


def graph(
    root: pathlib.Path,
    project: str = "",
    fmt: str = "json",
    *,
    entries: Sequence[registry.Entry] | None = None,
) -> GraphOut:
    """The dependency graph: one project and everything it reaches either way, or the whole registry."""
    if fmt not in ("json", "mermaid"):
        raise QueryError("format must be json or mermaid")
    everyone = {e.name: e for e in _entries(entries)}
    if project and project not in everyone:
        raise QueryError(f"no project {project!r} in the registry")
    depends = {name: list(e.depends) for name, e in everyone.items()}
    chosen = set(everyone)
    if project:
        needed_by: dict[str, set[str]] = {}
        for name, deps in depends.items():
            for dep in deps:
                needed_by.setdefault(dep, set()).add(name)
        chosen = {project}
        for links in (depends, needed_by):
            seen = {project}
            todo = [project]
            while todo:
                for nxt in links.get(todo.pop(), ()):
                    if nxt not in seen:
                        seen.add(nxt)
                        todo.append(nxt)
            chosen |= seen
    names = sorted(
        (n for n in chosen if n in everyone), key=lambda n: _rank(everyone[n])
    )
    unknown = sorted({d for n in names for d in depends[n] if d not in everyone})
    sub = {n: [d for d in depends[n] if d in chosen and d in everyone] for n in names}
    cycles = _cycles(names, sub)
    looped = {n for c in cycles for n in c}
    # Kahn's algorithm, always taking the earliest-ranked ready project, so the order is deterministic.
    waiting = {n: set(sub[n]) for n in names}
    order: list[str] = []
    while True:
        ready = [n for n in names if n not in order and not waiting[n]]
        if not ready:
            break
        nxt = ready[0]
        order.append(nxt)
        for n in names:
            waiting[n].discard(nxt)
    nodes: list[GraphNode] = [
        {
            "project": n,
            "kind": everyone[n].kind,
            "tier": everyone[n].tier,
            "wave": everyone[n].wave,
            "depends": list(everyone[n].depends),
            "open_rows": open_rows(root, n),
            "in_cycle": n in looped,
        }
        for n in names
    ]
    edges: list[GraphEdge] = [
        {"project": n, "depends_on": d} for n in names for d in depends[n]
    ]
    return {
        "project": project,
        "format": fmt,
        "nodes": nodes,
        "edges": edges,
        "order": order,
        "unordered": [n for n in names if n not in order],
        "cycles": cycles,
        "unknown": unknown,
        "mermaid": mermaid(nodes, edges) if fmt == "mermaid" else "",
    }


# -- plan -------------------------------------------------------------------------------------------


class ReadyItem(TypedDict):
    project: str
    row_id: str
    milestone: str
    named_test: str
    test_state: str
    why: str


class BlockedItem(TypedDict):
    project: str
    row_id: str
    on: str  # "dependency" | "lease block" | "decision"
    detail: str
    why: str


class WipProject(TypedDict):
    project: str
    active: bool
    live_leases: int
    holders: list[str]
    why: str


class WipOut(TypedDict):
    limit: int
    ok: bool
    projects: list[WipProject]
    why: str


class OldItem(TypedDict):
    project: str
    row_id: str
    title: str
    state: str
    since: str
    why: str


class PlanOut(TypedDict):
    active: list[str]
    ready: list[ReadyItem]
    ready_total: int
    ready_truncated: bool
    blocked: list[BlockedItem]
    wip: WipOut
    oldest: list[OldItem]
    oldest_total: int
    oldest_truncated: bool


def _line_dates(folder: pathlib.Path, path: pathlib.Path) -> dict[int, int]:
    """1-based line -> when it last changed (epoch seconds), from git blame; empty when not under git."""
    done = subprocess.run(  # noqa: S603, S607
        ["git", "-C", str(folder), "blame", "--line-porcelain", "--", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if done.returncode != 0:
        return {}
    dates: dict[int, int] = {}
    line = 0
    for text in done.stdout.splitlines():
        head = text.split(" ")
        if len(head) >= 3 and re.fullmatch(r"[0-9a-f]{40}", head[0]):
            line = int(head[2])
        elif text.startswith("author-time ") and line:
            dates[line] = int(text.split(" ", 1)[1])
    return dates


def _open_decisions(root: pathlib.Path, names: set[str]) -> list[registry.Decision]:
    path = root / "PORTFOLIO.md"
    if not path.is_file():
        return []
    return [
        d
        for d in registry.decisions(path.read_text(encoding="utf-8"), names)
        if not d.decided
    ]


def plan(
    root: pathlib.Path,
    active: set[str],
    *,
    leases: Leases | None = None,
    entries: Sequence[registry.Entry] | None = None,
    limit: int = 10,
) -> PlanOut:
    """Ready, blocked, work in progress and the oldest open rows, each with why it is listed."""
    from projecthub.mcp import read

    everyone = sorted(_entries(entries), key=_rank)
    names = {e.name for e in everyone}
    held = read_leases() if leases is None else leases
    cap = max(1, min(int(limit), MAX_LIMIT))
    result = read.ready_rows(active, "", cap, root=root, leased=set(held.live))
    raw_ready = result["ready"] if isinstance(result["ready"], list) else []
    ready: list[ReadyItem] = [
        {
            "project": str(r["project"]),
            "row_id": str(r["row_id"]),
            "milestone": str(r["milestone"]),
            "named_test": str(r["named_test"]),
            "test_state": str(r["test_state"]),
            "why": str(r["why"]),
        }
        for r in raw_ready
    ]
    states = {e.name: registry.state(root, e) for e in everyone}
    unfinished: dict[str, list[dict[str, str]]] = {}
    paths: dict[str, pathlib.Path] = {}
    for entry in everyone:
        path = roadmap.matrix_path(root, entry.name)
        if path is None:
            continue
        rows = [
            r
            for r in roadmap.rows_of(path.read_text(encoding="utf-8"))
            if r["state"] in UNFINISHED
        ]
        if rows:
            unfinished[entry.name] = rows
            paths[entry.name] = path

    blocked: list[BlockedItem] = []
    decisions = _open_decisions(root, names)
    for entry in everyone:
        if entry.name not in unfinished:
            continue
        count = len(unfinished[entry.name])
        waits = [
            d
            for d in entry.depends
            if states.get(d) is None or states[d].label != "Implemented"
        ]
        if waits:
            labels = ", ".join(
                f"{d} ({states[d].label if d in states else 'not in the registry'})"
                for d in waits
            )
            blocked.append(
                {
                    "project": entry.name,
                    "row_id": "",
                    "on": "dependency",
                    "detail": ", ".join(waits),
                    "why": f"{count} open row(s) wait on {labels}: a dependency must be code first",
                }
            )
        for decision in decisions:
            if entry.name in decision.projects:
                blocked.append(
                    {
                        "project": entry.name,
                        "row_id": "",
                        "on": "decision",
                        "detail": f"Decision {decision.number}: {decision.title.replace('`', '')}",
                        "why": "PORTFOLIO.md section 9 names this project in a decision not yet marked Decided",
                    }
                )
        for row in unfinished[entry.name]:
            key = f"{entry.name}:{row['name']}"
            if key in held.blocked:
                blocked.append(
                    {
                        "project": entry.name,
                        "row_id": row["name"],
                        "on": "lease block",
                        "detail": held.blocked[key],
                        "why": "its latest lease ended blocked: it waits for a person, not the next agent",
                    }
                )

    per_project: dict[str, list[str]] = {}
    for key, claim in held.live.items():
        per_project.setdefault(key.rpartition(":")[0], []).append(claim["holder"])
    busy = sorted(set(per_project) | set(active))
    wip_projects: list[WipProject] = []
    for name in busy:
        count = len(per_project.get(name, []))
        is_active = name in active
        if count and not is_active:
            why = f"{count} live lease(s) on a project the board does not mark Active"
        elif count:
            why = f"Active, {count} live lease(s)"
        else:
            why = "Active, nothing leased: capacity for work"
        wip_projects.append(
            {
                "project": name,
                "active": is_active,
                "live_leases": count,
                "holders": sorted(set(per_project.get(name, []))),
                "why": why,
            }
        )
    working = sorted(per_project)
    over = [p for p in working if p not in active]
    ok = len(working) <= ACTIVE_LIMIT and len(active) <= ACTIVE_LIMIT and not over
    if ok:
        wip_why = f"{len(working)} project(s) under lease, {len(active)} Active: within the rule of {ACTIVE_LIMIT}"
    else:
        problems = []
        if len(working) > ACTIVE_LIMIT:
            problems.append(f"{len(working)} projects under lease")
        if len(active) > ACTIVE_LIMIT:
            problems.append(f"{len(active)} projects marked Active")
        if over:
            problems.append(f"leases outside Active: {', '.join(over)}")
        wip_why = f"the two-active-projects rule is broken: {'; '.join(problems)}"

    aged: list[tuple[tuple[int, int, int, int], OldItem]] = []
    for position, entry in enumerate(everyone):
        if entry.name not in unfinished:
            continue
        path = paths[entry.name]
        dates = _line_dates(path.parent, path)
        for row in unfinished[entry.name]:
            line = int(row.get("line") or 0)
            stamp = dates.get(line)
            since = (
                time.strftime("%Y-%m-%d", time.gmtime(stamp))
                if stamp is not None
                else ""
            )
            why = (
                f"its line in {path.name} is unchanged since {since} (git blame)"
                if since
                else f"age unknown: {path.name} is not under git, so listed in plan order"
            )
            aged.append(
                (
                    (0 if since else 1, stamp or 0, position, line),
                    {
                        "project": entry.name,
                        "row_id": row["name"],
                        "title": row["title"],
                        "state": row["state"],
                        "since": since,
                        "why": why,
                    },
                )
            )
    aged.sort(key=lambda pair: pair[0])
    return {
        "active": sorted(active),
        "ready": ready,
        "ready_total": int(result["ready_total"]),  # type: ignore[call-overload]
        "ready_truncated": bool(result["truncated"]),
        "blocked": blocked,
        "wip": {
            "limit": ACTIVE_LIMIT,
            "ok": ok,
            "projects": wip_projects,
            "why": wip_why,
        },
        "oldest": [item for _, item in aged[:cap]],
        "oldest_total": len(aged),
        "oldest_truncated": len(aged) > cap,
    }


# -- command line -----------------------------------------------------------------------------------


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max([len(h), *(len(r[i]) for r in rows)]) for i, h in enumerate(headers)]
    lines = [
        "  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip()
    ]
    lines.append("  ".join("-" * w for w in widths))
    lines += [
        "  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip()
        for r in rows
    ]
    return "\n".join(lines)


def _short(text: str, width: int = 60) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def _query_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub query",
        description="projecthub: rows across every project's matrix that match all the filters given",
    )
    parser.add_argument("text", nargs="?", default="", help="search the id and title")
    parser.add_argument("--project", default="")
    parser.add_argument("--state", choices=STATES)
    parser.add_argument("--milestone", default="")
    parser.add_argument("--wave", type=int)
    parser.add_argument("--tier", type=int)
    parser.add_argument("--kind", default="")
    parser.add_argument(
        "--has-test", choices=[s.replace(" ", "_") for s in TEST_STATES]
    )
    parser.add_argument("--claimed", choices=("true", "false"))
    parser.add_argument("--blocked", choices=("true", "false"))
    parser.add_argument("--spec", default="", help="the spec cell contains this text")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--root", type=pathlib.Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    filters: dict[str, object] = {
        "project": args.project,
        "state": args.state,
        "milestone": args.milestone,
        "wave": args.wave,
        "tier": args.tier,
        "kind": args.kind,
        "has_test": args.has_test,
        "claimed": args.claimed,
        "blocked": args.blocked,
        "spec_contains": args.spec,
        "text": args.text,
    }
    try:
        out = query(args.root or default_root(), filters, args.limit)
    except QueryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    rows = [
        [
            r["project"],
            r["row_id"],
            r["state"],
            r["milestone"],
            r["test_state"],
            (
                f"claimed by {r['claim']['holder']}"
                if r["claim"]
                else ("blocked" if r["blocked"] else "")
            ),
            _short(r["title"]),
        ]
        for r in out["rows"]
    ]
    print(
        _table(["project", "row", "state", "milestone", "test", "lease", "title"], rows)
    )
    shown = len(out["rows"])
    print(
        f"\n{shown} of {out['total']} row(s)"
        + (" (truncated: raise --limit)" if out["truncated"] else "")
    )
    return 0


def _graph_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="projecthub graph",
        description="projecthub: project dependencies, their order and any cycles",
    )
    parser.add_argument("--project", default="")
    parser.add_argument("--mermaid", action="store_true", help="print a graph TD")
    parser.add_argument("--root", type=pathlib.Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        out = graph(
            args.root or default_root(),
            args.project,
            "mermaid" if args.mermaid else "json",
        )
    except QueryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    if args.mermaid:
        print(out["mermaid"], end="")
        return 0
    position = {n: i + 1 for i, n in enumerate(out["order"])}
    rows = [
        [
            str(position.get(n["project"], "-")),
            n["project"],
            str(n["wave"]),
            str(n["tier"]),
            str(n["open_rows"]),
            ", ".join(n["depends"]),
        ]
        for n in sorted(out["nodes"], key=lambda n: position.get(n["project"], 10_000))
    ]
    print(_table(["#", "project", "wave", "tier", "open", "depends on"], rows))
    for cycle in out["cycles"]:
        print(f"\ncycle: {' <-> '.join(cycle)} (these projects cannot be ordered)")
    if out["unknown"]:
        print(f"\nnot in the registry: {', '.join(out['unknown'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["graph"]:
        return _graph_cli(args[1:])
    if args[:1] == ["query"]:
        return _query_cli(args[1:])
    print("usage: projecthub query [...] | projecthub graph [...]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
