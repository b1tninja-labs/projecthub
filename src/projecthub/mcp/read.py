"""projecthub's planning surface: read the portfolio, claim work, and update a row - with evidence.

Why this is small. Task Master ships 36 MCP tools and documents that they cost about 21,000 tokens of
context, offering a 7-tool "core" mode at about 5,000. A tool list is not free: every schema is context
not spent on the code. Five tools are exposed, each returning evidence rather than advice.

Why there is no deliberation tool. The failures this portfolio keeps hitting are not "thought too
briefly": they are assertions made without checking - an invented NSS constant, a claimed roadmap status
never read from the file, a check that passed against a stale build artifact. More fluency about a wrong
fact is still wrong. So the write path here is guarded and the read path always names the file or command
it came from.

What is deliberately absent: a task store. beads keeps issues in a Dolt database and Task Master in a
.taskmaster file; either would become a second source of truth for obligations, and ROADMAP.md plus its
named tests is the authority CI can enforce. `claim` and `propose_status` move that authority, they do not
copy it.

Where the writes live (the MCP-writes decision, 2026-11-07: docs/research-mcp-planning-quality.md slice 1). The MCP server is read-only: every tool carries
readOnlyHint, and `propose_status` previews a change and runs the guard but cannot write. The two
writes - flipping a row and claiming its issue - are this module's command line, so each one is a
deliberate act a person or a script runs, not a tool an agent reaches for mid-turn:

    python -m projecthub.mcp.read status <project> <row> <status> [--write]
    python -m projecthub.mcp.read claim <project> <row> <login>

The reason is the evidence rule. The guard checks that a named test exists; it cannot check that the test
would fail if the behaviour regressed, so a write reachable from a tool call is a done row one confident
sentence away.
"""

from __future__ import annotations

from projecthub import config, paths

import json
import pathlib
import subprocess
import sys
from typing import Any, TypedDict

from projecthub.db import parser as roadmap
from projecthub.db.parser import rows_of
from projecthub.mcp import query as hubquery
from projecthub.github.sync import MARKER, PROJECTS, STATUSES, gh, issue_home, owned_by

# Mirrors `projecthub sync --root`/PROJECTHUB_ROOT. Importing ROOT from verify.py instead would have hardcoded
# this machine's layout into every read and write; the planner's own tests discovered that by passing
# here and failing in CI, where D:/code does not exist.
ROOT = pathlib.Path(paths.env("ROOT", "D:/code"))

# The owner used to name a project's repository. Moved repositories resolve from here through GitHub's
# redirect, and some stay personal on purpose; the Project's own owner is config.PROJECT_OWNER.
ORG = "b1tninja"
OPENISH = ("open",)


def _gh_json(argv: list[str]) -> object:
    done = subprocess.run(
        [gh(), *argv], capture_output=True, text=True, encoding="utf-8", check=False
    )  # noqa: S603
    if done.returncode != 0:
        return {"error": done.stderr.strip()[:200]}
    try:
        return json.loads(done.stdout or "null")
    except json.JSONDecodeError:
        return {"error": "unreadable response"}


def roadmap_path(project: str) -> pathlib.Path:
    """The project's matrix (ROADMAP.md, PLAN.md or docs/roadmap.md, per roadmap.matrix_path)."""
    return (
        roadmap.matrix_path(ROOT, project)
        or roadmap.project_dir(ROOT, project) / "ROADMAP.md"
    )


def portfolio_status(with_issues: bool = True) -> list[dict[str, object]]:
    """Every project with a matrix: rows by status, and the derived issue count when reachable."""
    out: list[dict[str, object]] = []
    for name in dict.fromkeys([*PROJECTS, *roadmap.HAND_OWNED]):
        path = roadmap_path(name)
        if not path.is_file():
            continue
        rows = rows_of(path.read_text(encoding="utf-8"))
        if not rows:
            continue
        tally: dict[str, int] = {}
        for row in rows:
            action = STATUSES.get(row["status"], ("status:unimplemented", "open"))[1]
            tally[action] = tally.get(action, 0) + 1
        entry: dict[str, object] = {
            "project": name,
            "rows": len(rows),
            "by_state": tally,
        }
        if with_issues:
            listed = _gh_json(
                [
                    "issue",
                    "list",
                    "--repo",
                    issue_home(f"{ORG}/{name}"),
                    "--state",
                    "open",
                    "--limit",
                    "1000",
                    "--json",
                    "number,title",
                ]
            )
            entry["open_issues"] = (
                sum(1 for i in listed if owned_by(str(i.get("title", "")), name))
                if isinstance(listed, list)
                else listed
            )
        out.append(entry)
    return out


def next_actions(project: str, limit: int = 10) -> list[dict[str, str]]:
    """Unmet obligations, each carrying the test that would prove it done.

    Done rows are excluded on purpose: they have no work item because the test carries the claim, which
    is also why a milestone's completion percentage cannot be trusted here.
    """
    path = roadmap_path(project)
    if not path.is_file():
        return [{"error": f"{project} has no ROADMAP.md"}]
    rows = [
        row
        for row in rows_of(path.read_text(encoding="utf-8"))
        if STATUSES.get(row["status"], ("", "open"))[1] == "open"
    ]
    out = []
    for row in rows[:limit]:
        node, state = roadmap.named_test(ROOT, project, row["where"], row["evidence"])
        out.append(
            {
                "row": row["name"],
                "status": row["status"],
                "spec": row["spec"],
                "where": row["where"],
                "named_test": node,
                "test_state": state,
                "milestone": row["milestone"] or "",
                "evidence": row["evidence"],
            }
        )
    return out


UNPLACED = 10_000


def ready_rows(
    active: set[str],
    project: str = "",
    limit: int = 10,
    root: pathlib.Path | None = None,
    leased: set[str] | frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Open rows an agent may start now, ranked, and how many were held back and why.

    A row is ready when its project is owner-Active (the two-project rule), is a package rather than a plan
    directory, and depends on nothing that is not yet code. Ranking: rows whose named test already exists
    first, then milestone order on the project's own ladder, then how many projects wait on this one.

    A row with a live lease (leases.py) is held back as in progress. One deliberate departure from the
    research design: a row whose test is "(to be written)" stays ready, ranked after written ones, because
    for a generated shell writing the test is the first step of the work, and the lease an agent then takes
    is what marks it in progress.
    """
    from projecthub import registry as portfolio

    base = root or ROOT
    entries = portfolio.entries()
    states = {e.name: portfolio.state(base, e) for e in entries}
    waiting: dict[str, int] = {}
    for e in entries:
        for dep in e.depends:
            waiting[dep] = waiting.get(dep, 0) + 1
    skipped: dict[str, int] = {}
    ready: list[tuple[tuple[int, int, int, str, int], dict[str, object]]] = []

    def skip(reason: str, count: int) -> None:
        if count:
            skipped[reason] = skipped.get(reason, 0) + count

    for entry in entries:
        if project and entry.name != project:
            continue
        path = roadmap.matrix_path(base, entry.name)
        if path is None:
            continue
        open_rows = [
            r
            for r in rows_of(path.read_text(encoding="utf-8"))
            if STATUSES.get(r["status"], ("", "open"))[1] == "open"
        ]
        if entry.name not in active:
            skip("project not Active", len(open_rows))
            continue
        folder = roadmap.project_dir(base, entry.name)
        if entry.kind == "Plan" or not (folder / "pyproject.toml").is_file():
            skip("plan directory: the deliverable is a document", len(open_rows))
            continue
        blockers = [
            d
            for d in entry.depends
            if states.get(d) is None or states[d].label != "Implemented"
        ]
        if blockers:
            skip(f"blocked by {', '.join(blockers)} (not yet code)", len(open_rows))
            continue
        order = list(roadmap.ladder_for(base, entry.name))
        mine = [r for r in open_rows if f"{entry.name}:{r['name']}" not in leased]
        skip("leased: in progress", len(open_rows) - len(mine))
        for row in mine:
            node, test_state = roadmap.named_test(
                base, entry.name, row["where"], row["evidence"]
            )
            rank_m = (
                order.index(row["milestone"]) if row["milestone"] in order else UNPLACED
            )
            unblocks = waiting.get(entry.name, 0)
            why = [f"{entry.name} is Active"]
            why.append(
                f"milestone {row['milestone']}"
                if row["milestone"]
                else "not on the ladder"
            )
            if unblocks:
                why.append(f"{unblocks} project(s) wait on {entry.name}")
            why.append(f"named test {test_state}")
            key = (
                0 if test_state == "exists" else 1,
                rank_m,
                -unblocks,
                entry.name,
                int(row.get("line") or 0),
            )
            ready.append(
                (
                    key,
                    {
                        "project": entry.name,
                        "row_id": row["name"],
                        "status": row["status"],
                        "where": row["where"],
                        "named_test": node,
                        "test_state": test_state,
                        "spec": row["spec"],
                        "milestone": row["milestone"] or "",
                        "unblocks": unblocks,
                        "why": "; ".join(why),
                    },
                )
            )
    ready.sort(key=lambda pair: pair[0])
    cap = max(1, limit)
    return {
        "active": sorted(active),
        "ready": [r for _, r in ready[:cap]],
        "ready_total": len(ready),
        "truncated": len(ready) > cap,
        "skipped": dict(sorted(skipped.items())),
    }


def active_projects(
    owner: str = config.PROJECT_OWNER, number: int = config.PROJECT_NUMBER
) -> set[str]:
    """Projects the owner marked Active on the board. Read, never inferred: Active is the owner's call."""
    from projecthub.github.client import Projects

    return {
        item.title.split(":", 1)[0].strip()
        for item in Projects().items(owner, number)
        if item.values.get("Level") == "Project"
        and item.values.get("Active") == "Active"
    }


def context_for(project: str, needle: str, window: int = 14) -> str:
    """The prose around a row's first mention - blocked notes and dead ends live here, not in the matrix.

    M7's four unreachable Microsoft documents are recorded in a paragraph, not a table. An agent that
    reads only the matrix re-runs the dead ends, which is the thing the document exists to prevent.
    """
    path = roadmap_path(project)
    if not path.is_file():
        return f"{project} has no ROADMAP.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if needle and needle in line:
            start = max(0, index - 2)
            return "\n".join(lines[start : index + window])
    return f"no mention of {needle!r} in {project}/ROADMAP.md"


def test_evidence(project: str, where: str, evidence: str = "") -> tuple[bool, str]:
    """Does the row's named test actually exist and define the named symbol?

    One implementation, shared with the issue text (rowdoc.py): two copies of this check would drift, and
    this is the one the done-row guard rests on. It is a floor, not the rule: the portfolio's rule is that
    a row flips only when the test would fail if the behaviour regressed, and only CI running that test
    can show that.
    """
    return roadmap.evidence(ROOT, project, where, evidence)


def rewrite_row(text: str, project: str, name: str, new_status: str) -> tuple[str, str]:
    """Return (new text, refusal). Empty refusal means the row was changed.

    Only the Status cell's token is replaced. Rebuilding the line from its stripped cells, as an earlier
    version did, re-spaced every cell and split an escaped pipe into two cells, so a one-word status
    change arrived as a whole-line diff. The row is found, and the done guard reads its Where and
    Evidence cells, through roadmap.py's header-aware grammar.
    """
    if new_status not in STATUSES:
        return (
            text,
            f"{new_status!r} is not a status this portfolio recognises: {sorted(STATUSES)}",
        )
    row = next((r for r in rows_of(text) if r["name"] == name), None)
    if row is None:
        return text, f"no row named {name} in {project}/{roadmap_path(project).name}"
    if row["status"] == "[no]":
        return (
            text,
            f"{name} is a declared non-goal; that is a decision, not a status to set",
        )
    if new_status in ("[ok]", "[x]"):
        ok, why = test_evidence(project, row["where"], row["evidence"])
        if not ok:
            return text, f"refusing to mark {name} done: {why}"
    lines = text.splitlines(keepends=True)
    number = int(row["line"])
    line = lines[number - 1]
    body = line.rstrip("\r\n")
    column = roadmap.status_column(text, number)
    lines[number - 1] = (
        roadmap.replace_status(body, new_status, column) + line[len(body) :]
    )
    return "".join(lines), ""


def propose_status(project: str, name: str, new_status: str, write: bool) -> str:
    """Preview, or apply, a row status change. The guard runs either way."""
    path = roadmap_path(project)
    if not path.is_file():
        return f"{project} has no ROADMAP.md"
    text = path.read_text(encoding="utf-8")
    updated, refusal = rewrite_row(text, project, name, new_status)
    if refusal:
        return f"REFUSED: {refusal}"
    if not write:
        return f"would write (pass write=true): {name} -> {new_status}\n\n{context_for(project, name, 3)}"
    path.write_text(updated, encoding="utf-8")
    return f"{project}/{path.name}: {name} -> {new_status}. Commit it with the code and the named test."


def _gh_edit_assignee(repo: str, number: str, flag: str, who: str) -> str:
    """Add or remove one assignee. Returns the failure text, or "" on success."""
    done = subprocess.run(  # noqa: S603
        [gh(), "issue", "edit", number, "--repo", repo, flag, who],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return "" if done.returncode == 0 else done.stderr.strip()[:200]


def _assignees(repo: str, number: str) -> list[str] | str:
    """The issue's current assignees, sorted, or an error string. Read fresh, never from the earlier list."""
    view = _gh_json(["issue", "view", number, "--repo", repo, "--json", "assignees"])
    if not isinstance(view, dict) or "assignees" not in view:
        return (
            str(view.get("error", "unreadable"))
            if isinstance(view, dict)
            else "unreadable"
        )
    return sorted(str(a["login"]) for a in view["assignees"] if a.get("login"))


def claim(project: str, name: str, who: str) -> str:
    """Assign the row's issue to `who`, refusing if someone else already holds it."""
    repo = issue_home(f"{ORG}/{project}")
    listed = _gh_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "1000",
            "--json",
            "number,title,url,body,assignees",
        ]
    )
    if not isinstance(listed, list):
        return f"could not read issues: {listed}"
    target = next(
        (
            i
            for i in listed
            if MARKER.format(name) in str(i.get("body", ""))
            and owned_by(str(i.get("title", "")), project)
        ),
        None,
    )
    if target is None:
        return f"no open issue carries {MARKER.format(name)}; run sync_roadmap --apply first"
    number = str(target["number"])
    holders = sorted(
        str(a["login"]) for a in target.get("assignees", []) if a.get("login")
    )
    if holders == [who]:
        return f"{target['url']} is already yours"
    if holders:
        return f"REFUSED: {project}#{number} is already claimed by {', '.join(holders)}"
    failure = _gh_edit_assignee(repo, number, "--add-assignee", who)
    if failure:
        return f"could not claim: {failure}"
    # The list above and the edit are two requests, so two agents can both pass the check and both be
    # added. GitHub offers no compare-and-swap on assignees, so the claim is only real once a re-read shows
    # the caller as the sole assignee. A contender that sees anyone else backs off, which means two
    # simultaneous claims may both fail: that is the safe outcome, and a retry resolves it.
    after = _assignees(repo, number)
    if isinstance(after, str):
        return f"UNCONFIRMED: assigned {who} but could not re-read the issue ({after}); check before working"
    if after == [who]:
        return f"claimed {target['url']} for {who}"
    _gh_edit_assignee(repo, number, "--remove-assignee", who)
    others = [login for login in after if login != who]
    return (
        f"REFUSED: {project}#{number} was contended by {', '.join(others) or 'someone'}; "
        f"{who} backed off. Retry in a moment"
    )


PACKET_LIMIT = 50_000


def _live_leases() -> set[str]:
    """Row keys under a live lease. No lease database yet means no leases, not an error."""
    from projecthub.mcp import work as leases

    return leases.Store().live_keys()


def _live_lease_rows() -> dict[str, dict[str, str]]:
    from projecthub.mcp import work as leases

    return {
        x.row_key: {
            "lease_id": x.lease_id,
            "holder": x.holder,
            "expires_at": leases._iso(x.expires_at),
        }
        for x in leases.Store().live()
    }


def _git_lines(folder: pathlib.Path, *args: str) -> list[str]:
    done = subprocess.run(  # noqa: S603, S607
        ["git", "-C", str(folder), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return done.stdout.splitlines() if done.returncode == 0 else []


def _find_row(
    row_ref: str, base: pathlib.Path
) -> tuple[Any, pathlib.Path, dict[str, str]]:
    """The portfolio entry, matrix file and row a reference names: project:row, or a row id unique across projects."""
    from projecthub import registry as portfolio

    entries = {e.name: e for e in portfolio.entries()}
    project, _, row_id = row_ref.rpartition(":")
    if project and project not in entries:
        raise LookupError(f"no project {project!r} in the portfolio")
    hits = []
    for name in [project] if project else sorted(entries):
        path = roadmap.matrix_path(base, name)
        if path is None:
            continue
        for row in rows_of(path.read_text(encoding="utf-8")):
            if row["name"] == row_id:
                hits.append((entries[name], path, row))
    if not hits:
        raise LookupError(f"no row {row_ref!r} in any obligation matrix")
    if len(hits) > 1:
        owners = ", ".join(sorted(h[0].name for h in hits))
        raise LookupError(f"{row_id!r} is a row of {owners}; use project:row")
    return hits[0]


def packet(
    row_ref: str,
    root: pathlib.Path | None = None,
    leases: dict[str, dict[str, str]] | None = None,
) -> dict[str, object]:
    """The context an agent needs to start a row, assembled from files, git and the lease store.

    It never carries specification text. The spec cell says which document governs the row; the agent reads
    the clause itself through rfclib, because a packet that paraphrased normative text would be the very
    "from memory" citation the workspace forbids.
    """
    from projecthub import registry as portfolio
    from projecthub.github import rowdoc

    base = root or ROOT
    entry, path, row = _find_row(row_ref, base)
    name = entry.name
    folder = roadmap.project_dir(base, name)
    if entry.kind == "Plan" or not (folder / "pyproject.toml").is_file():
        raise LookupError(
            f"{name} is a plan directory: its deliverable is a document, not code"
        )
    text = path.read_text(encoding="utf-8")
    key = f"{name}:{row['name']}"
    node, test_state = roadmap.named_test(base, name, row["where"], row["evidence"])
    ladder = roadmap.ladder_for(base, name)
    stub = rowdoc.stub_of(base, folder.name, row["name"])
    everyone = portfolio.entries()
    states = {e.name: portfolio.state(base, e) for e in everyone}
    depends = [
        {"project": d, "state": states[d].label if d in states else "Absent"}
        for d in entry.depends
    ]
    waiting = sorted(e.name for e in everyone if name in e.depends)
    decisions: list[str] = []
    portfolio_md = base / "PORTFOLIO.md"
    if portfolio_md.is_file():
        names = {e.name for e in everyone}
        decisions = [
            f"Decision {d.number}: {d.title.replace('`', '')}"
            for d in portfolio.decisions(
                portfolio_md.read_text(encoding="utf-8"), names
            )
            if not d.decided and name in d.projects
        ]
    agents = folder / "AGENTS.md"
    agent_lines = (
        agents.read_text(encoding="utf-8").splitlines() if agents.is_file() else []
    )
    rel = path.relative_to(folder).as_posix()
    history = _git_lines(
        folder, "log", "-n", "10", "--format=%h %as %s", "-S", row["title"], "--", rel
    )
    test_path = node.split("::", 1)[0] if "::" in node else ""
    package = folder.name.replace("-", "_")
    out: dict[str, object] = {
        "row_id": key,
        "project": name,
        "kind": entry.kind,
        "tier": entry.tier,
        "wave": entry.wave,
        "status": row["status"],
        "milestone": row["milestone"] or "",
        "milestone_text": ladder.get(row["milestone"], ""),
        "where": row["where"],
        "stub": (
            {
                "path": stub.path,
                "signature": stub.signature,
                "still_stub": stub.still_stub,
                "module": stub.module_summary,
            }
            if stub
            else None
        ),
        "named_test": node,
        "test_state": test_state,
        "spec": row["spec"],
        "spec_note": (
            "The Spec cell only. Read the governing clause with rfclib (or itulib for X.680/X.690) and cite it; "
            "never work from a paraphrase."
        ),
        "acceptance": {
            "test": (
                f"cd {folder.as_posix()} && python -m pytest -q -p no:cacheprovider {node}"
                if test_path
                else ""
            ),
            "gates": (
                f"cd {folder.as_posix()} && python -m pytest -q && python -m ruff check . && "
                f"python -m ruff format --check . && python -m mypy {package} tests"
            ),
            "done_when": (
                f"the {row['title']} row reads [ok] in {rel} in the same commit as the code and the named test, "
                "and that test fails when the behaviour is broken"
            ),
        },
        "depends_on": depends,
        "unblocks": waiting,
        "open_decisions": decisions,
        "invariants": {
            "file": agents.relative_to(base).as_posix() if agents.is_file() else "",
            "sections": [
                line.lstrip("# ").strip()
                for line in agent_lines
                if line.startswith("## ")
            ],
        },
        "notes": rowdoc.notes_for(text, row["title"], limit=5, width=4000),
        "history": history,
        "lease": (leases or {}).get(key),
        "evidence": _recorded_evidence(name, node),
        "files": [f for f in (rel, test_path, stub.path if stub else "") if f],
        "truncated": [],
    }
    return _fit(out)


def _recorded_evidence(project: str, node: str) -> dict[str, object] | None:
    """The node's result in the evidence record PROJECTHUB_EVIDENCE points at, if any (verify.py --json)."""
    record = paths.env("EVIDENCE")
    if not (record and node):
        return None
    try:
        data = json.loads(pathlib.Path(record).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for entry in data.get("projects", []):
        if entry.get("project") == project:
            hit = next(
                (t for t in entry.get("tests", []) if t.get("node_id") == node), None
            )
            return {
                "sha": entry.get("sha"),
                "dirty": entry.get("dirty"),
                "generated": data.get("generated"),
                "result": hit,
            }
    return None


def _fit(out: dict[str, object]) -> dict[str, object]:
    """Keep the packet under PACKET_LIMIT characters, cutting the longest optional parts first and saying so."""
    truncated: list[str] = []
    while len(json.dumps(out)) > PACKET_LIMIT:
        notes = out["notes"]
        history = out["history"]
        if isinstance(notes, list) and notes:
            longest = max(range(len(notes)), key=lambda i: len(notes[i]))
            if len(notes[longest]) > 400:
                notes[longest] = notes[longest][:400] + " ..."
            else:
                notes.pop()
            if "notes" not in truncated:
                truncated.append("notes")
        elif isinstance(history, list) and history:
            history.pop()
            if "history" not in truncated:
                truncated.append("history")
        else:
            break
    out["truncated"] = truncated
    return out


INSTRUCTIONS = (
    "If you arrive without a task, run `projecthub pickup`: its output is your assignment and it holds your claim. "
    "Work only that row. Portfolio planning for the protocol SDK workspace. Read tools return the file or command an answer "
    "came from. ROADMAP.md is authoritative and issues are derived from it, so a row is the plan and the "
    "named test is the proof. Never mark a row done because the code looks right: this tool refuses when "
    "the named test does not exist, and CI is still the only thing that can show the test would fail if "
    "the behaviour regressed. Normative text is never paraphrased from memory; if a specification is "
    "unreachable, say so on the issue and leave the row as it is."
)


class StatusOut(TypedDict):
    project: str
    row_count: int
    states: dict[str, int]
    open_issues: object


class StatusesOut(TypedDict):
    projects: list[StatusOut]


class PacketOut(TypedDict):
    row_id: str
    project: str
    kind: str
    tier: int
    wave: int
    status: str
    milestone: str
    milestone_text: str
    where: str
    stub: dict[str, object] | None
    named_test: str
    test_state: str
    spec: str
    spec_note: str
    acceptance: dict[str, str]
    depends_on: list[dict[str, str]]
    unblocks: list[str]
    open_decisions: list[str]
    invariants: dict[str, object]
    notes: list[str]
    history: list[str]
    lease: dict[str, str] | None
    evidence: dict[str, object] | None
    files: list[str]
    truncated: list[str]


class NextOut(TypedDict):
    active: list[str]
    ready: list[dict[str, object]]
    ready_total: int
    truncated: bool
    skipped: dict[str, int]


class ContextOut(TypedDict):
    project: str
    row: str
    source: str
    text: str


class PreviewOut(TypedDict):
    project: str
    row: str
    status: str
    would_write: bool
    context: str
    how_to_write: str


def build():
    """The MCP server. Imported here, not at module top, so the library works without mcp installed.

    The name matters: mcp 2.x renamed FastMCP to MCPServer, and every example on the internet still says
    `from mcp.server.fastmcp import FastMCP`. That import fails, and a client reports only that the
    server did not start.
    """
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    read_only = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
    app = MCPServer(name="projecthub", instructions=INSTRUCTIONS)

    @app.tool(
        name="status",
        description="Rows by state for every project with a matrix, plus open issue counts.",
        annotations=read_only,
        structured_output=True,
    )
    def status_tool(with_issues: bool = True) -> StatusesOut:
        rows = sorted(portfolio_status(with_issues), key=lambda r: str(r["project"]))
        return {
            "projects": [
                {
                    "project": str(r["project"]),
                    "row_count": int(r["rows"]),  # type: ignore[call-overload]
                    "states": dict(r["by_state"]),  # type: ignore[call-overload]
                    "open_issues": r.get("open_issues"),
                }
                for r in rows
            ]
        }

    @app.tool(
        name="next",
        description=(
            "Rows an agent may start now: open rows in owner-Active projects that are packages and depend on "
            "nothing unbuilt, ranked by named test written, milestone order, then projects unblocked. Says how "
            "many rows were held back and why. `active` overrides the board's Active field."
        ),
        annotations=read_only,
        structured_output=True,
    )
    def next_tool(
        project: str = "", limit: int = 10, active: list[str] | None = None
    ) -> NextOut:
        try:
            chosen = set(active) if active is not None else active_projects()
        except (
            Exception
        ) as error:  # the board read failed: say so, never return an empty list
            raise ToolError(f"could not read Active from the board: {error}") from error
        if not chosen:
            raise ToolError(
                "no project is marked Active on the board; set Active on at most two projects, or pass active=[...]"
            )
        return ready_rows(chosen, project, limit, leased=_live_leases())  # type: ignore[return-value]

    @app.tool(
        name="packet",
        description=(
            "Everything needed to start one row, in one answer under 50,000 characters: where it lives, the "
            "named test and whether it exists, the spec cell (never the spec's text: read that with rfclib), the "
            "exact acceptance commands, dependencies and their state, open decisions naming the project, the "
            "project's AGENTS.md sections, ROADMAP notes about the row, recent commits touching it, any live "
            "lease, and the latest recorded evidence. row_id is project:row or a row id unique across projects."
        ),
        annotations=read_only,
        structured_output=True,
    )
    def packet_tool(row_id: str) -> PacketOut:
        try:
            return packet(row_id, leases=_live_lease_rows())  # type: ignore[return-value]
        except LookupError as error:
            raise ToolError(str(error)) from error

    @app.tool(
        name="context",
        description="The prose around a row, where blocked notes and dead ends are recorded.",
        annotations=read_only,
        structured_output=True,
    )
    def context_tool(project: str, row: str) -> ContextOut:
        text = context_for(project, row)
        if text.startswith(f"{project} has no") or text.startswith("no mention of"):
            raise ToolError(text)
        return {
            "project": project,
            "row": row,
            "source": f"{project}/ROADMAP.md",
            "text": text,
        }

    @app.tool(
        name="propose_status",
        description=(
            "Preview a row status change and run the guard: refuses a done status whose named test does not "
            "exist, and refuses to change a declared non-goal. Never writes; the write is a CLI command."
        ),
        annotations=read_only,
        structured_output=True,
    )
    def propose_status_tool(project: str, row: str, status: str) -> PreviewOut:
        outcome = propose_status(project, row, status, write=False)
        if outcome.startswith("REFUSED:") or " has no ROADMAP.md" in outcome:
            raise ToolError(outcome)
        return {
            "project": project,
            "row": row,
            "status": status,
            "would_write": True,
            "context": context_for(project, row, 3),
            "how_to_write": f"python -m projecthub.mcp.read status {project} {row} '{status}' --write",
        }

    # -- projecthub query tools: Jira-style reads across projects (projecthub.mcp.query) ----------------

    @app.tool(
        description=(
            "Rows across every project's matrix matching all the filters given: project, state (open, part, "
            "done, nongoal), milestone, wave, tier, kind, has_test (exists, to be written, missing, none), "
            "claimed and blocked (from the lease store), spec_contains, and text over id and title. Each row "
            "carries its spec, named test and test state, and its claim or block. Sorted by wave, tier, project "
            "and matrix order; `truncated` says when `limit` cut the list, `total` how many matched."
        ),
        annotations=read_only,
        structured_output=True,
    )
    def query(
        project: str = "",
        state: str = "",
        milestone: str = "",
        wave: int | None = None,
        tier: int | None = None,
        kind: str = "",
        has_test: str = "",
        claimed: bool | None = None,
        blocked: bool | None = None,
        spec_contains: str = "",
        text: str = "",
        limit: int = 50,
    ) -> hubquery.QueryOut:
        filters: dict[str, object] = {
            "project": project,
            "state": state,
            "milestone": milestone,
            "wave": wave,
            "tier": tier,
            "kind": kind,
            "has_test": has_test,
            "claimed": claimed,
            "blocked": blocked,
            "spec_contains": spec_contains,
            "text": text,
        }
        try:
            return hubquery.query(hubquery.default_root(), filters, limit)
        except hubquery.QueryError as error:
            raise ToolError(str(error)) from error

    @app.tool(
        description=(
            "Project dependencies from the registry: edges (project depends_on), a topological order, every "
            "cycle (reported, never broken), dependencies the registry does not hold, and each project's open "
            "rows (open or part). `project` narrows to that project and everything it reaches either way. "
            "format=mermaid also returns a `graph TD` text with each dependency above what needs it."
        ),
        annotations=read_only,
        structured_output=True,
    )
    def graph(project: str = "", format: str = "json") -> hubquery.GraphOut:
        try:
            return hubquery.graph(hubquery.default_root(), project, format)
        except hubquery.QueryError as error:
            raise ToolError(str(error)) from error

    @app.tool(
        description=(
            "One summary of what to do now: ready rows (the same rules as next), what is blocked and on "
            "what (an unbuilt dependency, a lease that ended blocked, an open PORTFOLIO.md decision), work in "
            "progress per project against the two-active-projects rule, and the open rows unchanged longest "
            "(git blame). Every item says why. `active` overrides the board's Active field."
        ),
        annotations=read_only,
        structured_output=True,
    )
    def plan(active: list[str] | None = None, limit: int = 10) -> hubquery.PlanOut:
        try:
            chosen = set(active) if active is not None else active_projects()
        except (
            Exception
        ) as error:  # the board read failed: say so, never plan from an empty set
            raise ToolError(f"could not read Active from the board: {error}") from error
        return hubquery.plan(hubquery.default_root(), chosen, limit=limit)

    return app


def cli(argv: list[str]) -> int:
    """The two writes, as commands. Exit 1 on a refusal so a script cannot mistake it for success."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m projecthub.mcp.read", description="projecthub planner writes"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser(
        "status", help="preview, or with --write apply, a row status change"
    )
    status.add_argument("project")
    status.add_argument("row")
    status.add_argument("status")
    status.add_argument("--write", action="store_true")
    claiming = sub.add_parser(
        "claim", help="assign the row's issue to a login, confirmed by a re-read"
    )
    claiming.add_argument("project")
    claiming.add_argument("row")
    claiming.add_argument("who")
    args = parser.parse_args(argv)
    if args.command == "status":
        outcome = propose_status(args.project, args.row, args.status, args.write)
    else:
        outcome = claim(args.project, args.row, args.who)
    print(outcome)
    refused = outcome.startswith(
        ("REFUSED", "UNCONFIRMED", "could not", "no open issue")
    ) or (" has no " in outcome)
    return 1 if refused else 0


def main() -> None:
    import anyio

    anyio.run(build().run_stdio_async)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(cli(sys.argv[1:]))
    print(
        "projecthub planner: speaks MCP on stdio; run it from a client, or use the status/claim commands",
        file=sys.stderr,
    )
    main()
