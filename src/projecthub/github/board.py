"""Build the portfolio's Projects board at the grain the portfolio actually plans in.

Why it changed. The first two layouts filed obligation rows from the three repositories that happen to be on
GitHub and called that the portfolio. PORTFOLIO.md plans in something else: projects with a kind, a tier and a
wave; waves with an exit test ("a wave is not finished when it feels done"); arrows that mean "cannot
deliver behaviour without"; a rule that at most two projects carry real effort at once; and decisions only a
named human can make, which are what actually gates certledger's fetch layer. A board of rows from three
repositories could show none of that.

So the board is a tree, using GitHub's own sub-issues so every level gets a native progress bar:

    Wave (exit test)                         issue in b1tninja/sdk-forge, which publishes PORTFOLIO.md
      Project (kind, state, tier, rows)      issue in b1tninja/sdk-forge: every project, local-only ones too
        Milestone epic (Mn)                  issue in the project's own repository, when it has one
          Obligation row                     issue in the project's own repository (sync_roadmap.py)

and two kinds of native "blocked by" link: a project is blocked by a dependency that is not yet code, and by
an open decision from PORTFOLIO.md section 9 that names it. Field values come from portfolio.py, which reads
PORTFOLIO.md, forge.POSITION and the disk.

What it never does. It does not move Status forward: Ready, In progress, In review and Blocked are a
person's or an agent's call, and Active (the two-project rule) is never written by code. It does not invent
dates, estimates or milestones. It deletes nothing.

The board is chosen by number, never by title: two open boards once shared the title, and a lookup by
title lands on whichever GitHub lists first. The number comes from FORGE_BOARD, else BOARD_NUMBER.

Usage:
    python board.py                                  # plan against the configured board
    python board.py --number 4 --check               # read only; exit 1 on any drift
    python board.py --number 4 --project pkix        # touch pkix's card, epics and rows, nothing else
    python board.py --number 4 --apply
    python board.py --create "A new title" --apply   # refused when any board, closed too, has the title
"""

from __future__ import annotations

from projecthub import config

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from projecthub import registry as portfolio
from projecthub.db import parser as roadmap
from projecthub.github import rowdoc
from projecthub.github.client import Change, GhProjectsError, Item, Project, Projects
from projecthub.github.sync import STATUSES, gh, issue_home, owned_by, rows_of, shared

ROOT = pathlib.Path(os.environ.get("FORGE_ROOT") or "D:/code")
OWNER = config.PROJECT_OWNER
PORTFOLIO_REPO = config.PORTFOLIO_REPO
# There is one board. Configured in config.py and FORGE_BOARD, never looked up by title.
BOARD_NUMBER = int(os.environ.get("FORGE_BOARD") or config.PROJECT_NUMBER)
ISSUE_LIMIT = 1000

STATUS = [
    (
        "Triage",
        "PINK",
        "Proposed by an agent. Move it to Backlog to accept it into the plan.",
    ),
    ("Backlog", "GRAY", "Not started. For a row: the ROADMAP reads [ ] or [part]."),
    ("Ready", "BLUE", "Chosen for a cycle and unblocked. Claim it before starting."),
    ("In progress", "YELLOW", "Claimed: someone is working on it now."),
    ("In review", "PURPLE", "A pull request is open against it."),
    (
        "Blocked",
        "RED",
        "Waiting on a dependency, a document or a decision: say which in Blocked by.",
    ),
    ("Done", "GREEN", "Computed: an evidence record passes on the default branch."),
]
ALIASES = {"Backlog": ("Todo",), "In progress": ("In Progress",)}

LEVELS = [
    ("Wave", "PURPLE", "A wave of PORTFOLIO.md section 7, with its exit test."),
    ("Project", "BLUE", "One project of the portfolio, local-only ones included."),
    ("Decision", "RED", "A choice PORTFOLIO.md section 9 reserves for a named human."),
    ("Milestone", "ORANGE", "A milestone of a project's ROADMAP ladder."),
    ("Obligation", "GRAY", "One row of a project's obligation matrix."),
]
KINDS = [
    ("Library", "BLUE", "A protocol SDK."),
    ("Service", "PURPLE", "Hosted, not installed: certledger."),
    ("Plan", "YELLOW", "A plan directory: no package, deliberately."),
    ("Reference", "GREEN", "A reference corpus the citation rules depend on."),
    ("Tooling", "GRAY", "The forge itself."),
]
STATES = [
    ("Implemented", "GREEN", "Code and tests, and done rows where it has a matrix."),
    ("Shell", "YELLOW", "Generated: every public function raises NotImplementedError."),
    ("Plan only", "ORANGE", "Documents, no code."),
    ("Absent", "RED", "No directory in the workspace."),
]
PRIORITY = [
    ("P0", "RED", "Tier 1: work on now."),
    ("P1", "ORANGE", "Tier 2: next."),
    ("P2", "YELLOW", "Tier 3: planned."),
    ("P3", "GRAY", "Tier 4: hold."),
    ("P4", "GRAY", "Tier 5: candidate, not started."),
]
WAVES = [(f"Wave {n}", "BLUE", f"PORTFOLIO.md section 7, wave {n}.") for n in range(6)]
ACTIVE = [
    ("Active", "GREEN", "Carries real effort now. The rule is at most two projects.")
]
# Not "Type": GitHub reserves that name for its own issue-type field.
TYPES = [
    ("Epic", "PURPLE", "A wave, a milestone or a ladder rung."),
    ("Obligation", "BLUE", "Something verifiable, with a spec and a named test."),
    ("Task", "GRAY", "Needed work with no spec behaviour: CI, docs, a release."),
    ("Bug", "RED", "A regression or defect, found in another entry."),
    ("Question", "YELLOW", "An open question that blocks or shapes work."),
    ("Decision", "ORANGE", "Proposed, accepted or superseded by the owner."),
    ("Finding", "PINK", "Negative knowledge: a dead end, so nobody repeats it."),
    ("Rule", "GREEN", "An invariant shown in every packet for its project."),
]
# The worst status among an entry's citations, so a "Stale specs" view needs no body parsing.
CITATIONS = [
    ("Current", "GREEN", "Every citation resolves to a current document."),
    ("Errata", "ORANGE", "A cited section has verified errata."),
    ("Edition changed", "YELLOW", "A newer edition of a cited document exists."),
    ("Obsolete", "RED", "A cited document is obsolete; its successor is named."),
    ("Unresolved", "GRAY", "A citation has not been checked against its source."),
]
SELECTS = {
    "Level": LEVELS,
    "Kind": KINDS,
    "State": STATES,
    "Priority": PRIORITY,
    "Wave": WAVES,
    "Active": ACTIVE,
    "Entry type": TYPES,
    "Citations": CITATIONS,
}
NUMBERS = ("Rows done", "Rows open")
TEXT_FIELDS = ("Spec", "Named test", "Blocked by", "Agent", "Last activity")
DATE_FIELDS = ("Claimed",)
CYCLE_START = "2026-11-09"
CYCLE_DAYS = 14
CYCLES = 6

ROW_MARKER = re.compile(r"<!-- roadmap:(\S+) -->")
EPIC_MARKER = "<!-- roadmap-milestone:{} -->"
MARK = "<!-- portfolio:{}:{} -->"
PORTFOLIO_MARKER = re.compile(r"<!-- portfolio:\w+:\S+ -->")
# An absolute local path says where one laptop keeps its checkouts, which no reader of an issue can use,
# and it moves with FORGE_ROOT, so a body holding one is never stable.
LOCAL_PATH = re.compile(r"\b[A-Za-z]:[\\/](?:[\w.-]+[\\/])*")


def cycles(
    start: str = CYCLE_START, days: int = CYCLE_DAYS, count: int = CYCLES
) -> list[dict[str, object]]:
    first = datetime.date.fromisoformat(start)
    return [
        {
            "title": f"Cycle {n + 1}",
            "startDate": (first + datetime.timedelta(days=days * n)).isoformat(),
            "duration": days,
        }
        for n in range(count)
    ]


def options(
    spec: Sequence[tuple[str, str, str]], keep: dict[str, str] | None = None
) -> list[dict[str, str]]:
    """GraphQL option inputs. `keep` maps an existing option's name to its id, so a rename keeps values."""
    keep = keep or {}
    out = []
    for name, color, description in spec:
        option = {"name": name, "color": color, "description": description}
        for old in (name, *ALIASES.get(name, ())):
            if old in keep:
                option["id"] = keep[old]
                break
        out.append(option)
    return out


def priority(tier: int) -> str:
    return f"P{tier - 1}" if tier else ""


def unlocal(text: str) -> str:
    """`text` without absolute local paths: `D:/code/pkix/README.md` becomes `pkix/README.md`, and a path
    outside the workspace keeps only its file name."""
    for root in {str(ROOT), ROOT.as_posix()}:
        text = re.sub(re.escape(root.rstrip("/\\")) + r"[\\/]", "", text, flags=re.I)
    return LOCAL_PATH.sub("", text)


def short(text: str, limit: int = 72) -> str:
    """A title-sized clause from prose: markdown stripped, cut at a sentence or clause boundary."""
    plain = re.sub(r"[*`]", "", text)
    clause = re.split(r"(?<=[a-z0-9)])[.:;]\s|\s[-\u2013\u2014]\s", plain, maxsplit=1)[
        0
    ]
    plain = clause.strip().rstrip(".")
    if len(plain) <= limit:
        return plain
    return plain[:limit].rsplit(" ", 1)[0].rstrip(",;: ") + " ..."


# -- issue text -------------------------------------------------------------------------------------------


def project_title(entry: portfolio.Entry, state: portfolio.State) -> str:
    return f"{entry.name}: {entry.what or short(state.blurb) or entry.kind.lower()}"


def project_body(
    entry: portfolio.Entry,
    state: portfolio.State,
    depends: list[tuple[str, str]],
    gates: list[tuple[int, str]],
    rows: list[dict[str, str]],
    row_links: dict[str, str],
) -> str:
    tiering = (
        f"Tier {entry.tier}, wave {entry.wave}"
        if entry.tier and entry.wave
        else "untiered: not sequenced by PORTFOLIO.md"
    )
    counts = f" - {state.done} of {state.rows} obligations done" if state.rows else ""
    repo = (
        f"[`{state.repo}`](https://github.com/{state.repo})"
        if state.repo
        else f"local only - no GitHub repository yet (`{entry.directory}/` in the workspace)"
    )
    lines = [
        MARK.format("project", entry.name),
        "",
        f"**{entry.kind}** - **{state.label}**{counts} - {tiering}",
        "",
        f"**Repository:** {repo}",
        "",
    ]
    if state.blurb:
        lines += [state.blurb, ""]
    lines += ["## Depends on", ""]
    if depends:
        for name, label in depends:
            note = (
                "" if label == "Implemented" else " - **blocks this** until it is code"
            )
            lines.append(f"- `{name}`: {label}{note}")
    else:
        lines.append(
            "Nothing in the portfolio: its wave is the only thing holding it back."
        )
    lines.append("")
    if gates:
        lines += ["## Waiting on a decision", ""]
        lines += [f"- Decision {n}: {title}" for n, title in gates]
        lines.append("")
    if rows:
        lines += [
            f"## Obligations ({state.matrix})",
            "",
            "| Row | Status | Milestone |",
            "|---|---|---|",
        ]
        for row in rows:
            name = (
                f"[`{row['title']}`]({row_links[row['name']]})"
                if row["name"] in row_links
                else f"`{row['title']}`"
            )
            lines.append(f"| {name} | `{row['status']}` | {row['milestone'] or '-'} |")
        lines.append("")
    lines += [
        f"Sources: {entry.source}"
        + (f"; `{entry.directory}/{state.matrix}`" if state.matrix else "")
        + ". "
        "Regenerated by `sdk-forge/board.py`: change those files, not this issue.",
        "",
    ]
    return "\n".join(lines)


def wave_title(wave: portfolio.Wave) -> str:
    return f"Wave {wave.number}: {wave.title}"


def wave_body(
    wave: portfolio.Wave, members: list[tuple[portfolio.Entry, portfolio.State]]
) -> str:
    exit_test = (
        wave.exit
        or "None stated: this wave is the rest of the portfolio, in tier order, one at a time."
    )
    lines = [
        MARK.format("wave", wave.number),
        "",
        "## Exit test",
        "",
        f"> {exit_test}",
        "",
        "A wave is not finished when it feels done (PORTFOLIO.md section 7).",
        "",
        "## Projects",
        "",
    ]
    if members:
        for entry, state in members:
            counts = f", {state.done}/{state.rows} rows" if state.rows else ""
            lines.append(f"- `{entry.name}` - {state.label}{counts}")
    else:
        lines.append("None: this wave is hygiene, not features.")
    lines += [
        "",
        "Quoted from PORTFOLIO.md section 7 and regenerated by `sdk-forge/board.py`.",
        "",
    ]
    return "\n".join(lines)


def decision_title(decision: portfolio.Decision) -> str:
    return f"Decision {decision.number}: {re.sub(r'`', '', decision.title)}"


def decision_body(decision: portfolio.Decision) -> str:
    return "\n".join(
        [
            MARK.format("decision", decision.number),
            "",
            f"> {decision.body}",
            "",
            "Quoted from PORTFOLIO.md section 9, which reserves this choice for a named human. When it is made,",
            "record it there with a date (section 1) and close this issue, which releases the projects it blocks.",
            "",
        ]
    )


def epic_title(name: str, key: str, ladder: dict[str, str]) -> str:
    return f"[{name}] {rowdoc.milestone_title(key, ladder)}"


def epic_body(
    name: str,
    repo: str,
    key: str,
    ladder: dict[str, str],
    rows: list[dict[str, str]],
    numbers: dict[str, int],
) -> str:
    lines = [
        EPIC_MARKER.format(key),
        "",
        f"**Repository:** [`{repo}`](https://github.com/{repo})  |  **Milestone:** {key}",
        "",
        "## What this milestone delivers",
        "",
        ladder.get(key, "(not described on the ladder)"),
        "",
        "## Accepted when every row below reads `[ok]` with its named test",
        "",
    ]
    for row in rows:
        done = STATUSES.get(row["status"], ("", "open"))[1] == "close"
        ref = f" #{numbers[row['name']]}" if row["name"] in numbers else ""
        lines.append(f"- [{'x' if done else ' '}] `{row['title']}`{ref}")
    lines += [
        "",
        "The rows are this issue's sub-issues, so the progress bar is GitHub's own count. The ladder lives in",
        f"[ROADMAP.md](https://github.com/{repo}/blob/HEAD/ROADMAP.md) section 2; edit it there and re-run",
        "`sdk-forge/board.py`.",
        "",
    ]
    return "\n".join(lines)


# -- views ------------------------------------------------------------------------------------------------


def view_specs(ids: dict[str, int]) -> list[dict[str, object]]:
    """Every view is filtered by Level, so a project card never lands on the work board and a row never
    lands on the portfolio table. Field ids are the numeric REST ones."""

    def f(*names: str) -> list[int]:
        return [ids[n] for n in names if n in ids]

    def sort(name: str) -> list[list[object]]:
        return [[ids[name], "asc"]] if name in ids else []

    rows = f(
        "Title",
        "Repository",
        "Status",
        "Priority",
        "Milestone",
        "Wave",
        "Spec",
        "Named test",
        "Assignees",
    )
    return [
        {
            "name": "Portfolio",
            "layout": "table",
            "filter": "level:Project",
            "group_by": f("Priority"),
            "sort_by": sort("Wave"),
            "visible_fields": f(
                "Title",
                "Kind",
                "State",
                "Wave",
                "Active",
                "Rows done",
                "Rows open",
                "Sub-issues progress",
                "Status",
            ),
        },
        {
            "name": "Waves",
            "layout": "table",
            "filter": "level:Wave",
            "sort_by": sort("Wave"),
            "visible_fields": f("Title", "Sub-issues progress", "Status"),
        },
        {
            "name": "Decisions",
            "layout": "table",
            "filter": "level:Decision is:open",
            "visible_fields": f("Title", "Status", "Assignees"),
        },
        {
            "name": "Work board",
            "layout": "board",
            "filter": "level:Obligation",
            "vertical_group_by": f("Status"),
            "group_by": f("Repository"),
            "visible_fields": f(
                "Assignees", "Priority", "Milestone", "Linked pull requests"
            ),
        },
        {
            "name": "Backlog",
            "layout": "table",
            "filter": "level:Obligation -status:Done",
            "group_by": f("Priority"),
            "sort_by": sort("Wave"),
            "visible_fields": rows,
        },
        {
            "name": "Milestones",
            "layout": "table",
            "filter": "level:Milestone",
            "group_by": f("Repository"),
            "visible_fields": f(
                "Title", "Sub-issues progress", "Priority", "Wave", "Status"
            ),
        },
        {
            "name": "Current cycle",
            "layout": "board",
            "filter": "cycle:@current",
            "vertical_group_by": f("Status"),
            "visible_fields": f(
                "Assignees", "Repository", "Level", "Linked pull requests"
            ),
        },
        {
            "name": "Blocked",
            "layout": "table",
            "filter": "status:Blocked",
            "visible_fields": f(
                "Title", "Level", "Repository", "Blocked by", "Assignees"
            ),
        },
        {
            "name": "My items",
            "layout": "table",
            "filter": "assignee:@me -status:Done",
            "visible_fields": rows,
        },
        {
            "name": "Triage",
            "layout": "table",
            "filter": "status:Triage",
            "sort_by": [],
            "visible_fields": f(
                "Title", "Entry type", "Repository", "Spec", "Last activity"
            ),
        },
        {
            "name": "Stale specs",
            "layout": "table",
            "filter": 'citations:Obsolete,Errata,"Edition changed"',
            "sort_by": sort("Priority"),
            "visible_fields": f("Title", "Entry type", "Citations", "Spec", "Status"),
        },
    ]


README = """# Protocol SDK portfolio

The portfolio at the grain `PORTFOLIO.md` plans in, as one tree with GitHub's own progress bars:

**Wave** (with its exit test) -> **Project** (kind, state, tier, rows done) -> **Milestone** -> **Obligation**.

Waves, projects and decisions are issues in `sdk-forge`, the repository that publishes `PORTFOLIO.md`.
Milestones and obligation rows are issues in each project's own repository. A project is *blocked by* a
dependency that is not yet code, and by any open decision in `PORTFOLIO.md` section 9 that names it.

## Authority

`PORTFOLIO.md` decides sequencing and each project's `ROADMAP.md` decides protocol obligations. This board is
regenerated from them by `sdk-forge/sync_roadmap.py` and `sdk-forge/board.py`; editing a card's text is
overwritten. A card cannot make a row done: a row reads `[ok]` only in the same commit as the code and a named
test that would fail if the behaviour regressed.

## Views

- **Portfolio** - every project by priority (P0 is Tier 1), with kind, state and rows done.
- **Waves** - each wave's exit test and how far its projects are.
- **Decisions** - what is waiting on a person, not on code.
- **Work board** - obligation rows by status, one swimlane per repository.
- **Backlog**, **Milestones**, **Current cycle**, **Blocked**, **My items**.

## What is yours to set

**Active** (at most two projects carry real effort at once), Status beyond Backlog and Done, and Cycle.
"""
SHORT = "Waves, projects, decisions and obligations of the protocol SDK portfolio, from PORTFOLIO.md and each ROADMAP.md."


# -- GitHub -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Issue:
    number: int
    node_id: str
    url: str
    title: str
    body: str
    state: str
    milestone: str


class Issues(Protocol):
    """Issue reads and writes in one repository at a time. The board touches issues only through this."""

    def read(self, repo: str) -> list[Issue]: ...

    def create(self, repo: str, title: str, body: str, milestone: str) -> None: ...

    def edit(
        self, repo: str, number: int, title: str, body: str, milestone: str
    ) -> None: ...


class GhIssues:
    """Issues through the gh CLI."""

    def __init__(self, limit: int = ISSUE_LIMIT) -> None:
        self.limit = limit

    def milestones(self, repo: str) -> set[str]:
        """Titles of every milestone in the repository, open or closed."""
        done = subprocess.run(  # noqa: S603
            [
                gh(),
                "api",
                f"repos/{repo}/milestones?state=all",
                "--paginate",
                "--jq",
                ".[].title",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if done.returncode != 0:
            raise GhProjectsError(
                f"could not list milestones in {repo}: {done.stderr.strip()[:200]}"
            )
        return {line for line in done.stdout.splitlines() if line}

    def read(self, repo: str) -> list[Issue]:
        done = subprocess.run(  # noqa: S603
            [
                gh(),
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "all",
                "--limit",
                str(self.limit),
                "--json",
                "number,id,url,title,body,state,milestone",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if done.returncode != 0:
            raise GhProjectsError(
                f"could not list issues in {repo}: {done.stderr.strip()[:200]}"
            )
        listed = json.loads(done.stdout or "[]")
        if len(listed) >= self.limit:
            # A full page may be a truncated one, and a marker issue past the cut reads as missing, which
            # an apply run answers with a duplicate.
            raise GhProjectsError(
                f"{repo} returned {len(listed)} issues, the listing limit; refusing to plan from a "
                "possibly truncated list"
            )
        out = []
        for item in listed:
            milestone = item.get("milestone") or {}
            out.append(
                Issue(
                    int(item["number"]),
                    str(item["id"]),
                    str(item["url"]),
                    str(item["title"]),
                    str(item.get("body") or ""),
                    str(item["state"]),
                    str(milestone.get("title", "")),
                )
            )
        return out

    def create(self, repo: str, title: str, body: str, milestone: str) -> None:
        argv = ["issue", "create", "--repo", repo, "--title", title]
        self._with_body(argv + (["--milestone", milestone] if milestone else []), body)

    def edit(
        self, repo: str, number: int, title: str, body: str, milestone: str
    ) -> None:
        argv = ["issue", "edit", str(number), "--repo", repo, "--title", title]
        self._with_body(argv + (["--milestone", milestone] if milestone else []), body)

    def _with_body(self, argv: list[str], body: str) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "body.md"
            path.write_text(body, encoding="utf-8", newline="\n")
            done = subprocess.run(  # noqa: S603
                [gh(), *argv, "--body-file", str(path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if done.returncode != 0:
            raise GhProjectsError(f"{' '.join(argv[:3])}: {done.stderr.strip()[:200]}")


def same(a: str, b: str) -> bool:
    return a.replace("\r\n", "\n").strip() == b.replace("\r\n", "\n").strip()


FILE_AHEAD = "file-ahead"  # the files say something the board does not show yet
BOARD_AHEAD = "board-ahead"  # the board shows something the files do not say
ACTIVE_LIMIT = 2  # PORTFOLIO.md: at most two projects carry real effort at once


class Board:
    """One board and the issues behind it. With apply False nothing is written: every write it would make
    is printed and recorded in `drift`, which is what `--check` reports."""

    def __init__(
        self,
        client: Projects,
        owner: str,
        number: int,
        apply: bool,
        issues: Issues | None = None,
    ) -> None:
        self.client, self.owner, self.number, self.apply = client, owner, number, apply
        self.store: Issues = issues if issues is not None else GhIssues()
        self._issues: dict[str, list[Issue]] = {}
        self._milestones: dict[str, set[str]] = {}
        self.drift: list[tuple[str, str]] = []
        self.failures: list[str] = []

    def say(self, what: str) -> bool:
        """Announce a write. True means go ahead; in a plan or check run it is drift instead."""
        print(("    " if self.apply else "    would ") + what)
        if not self.apply:
            self.drift.append((FILE_AHEAD, what))
        return self.apply

    def note(self, kind: str, what: str) -> None:
        """Drift no write of this tool would cure: the board is ahead of the files, or a human rule is broken."""
        print(f"    {kind}: {what}")
        self.drift.append((kind, what))

    def fail(self, what: str) -> None:
        print(f"    FAILED {what}", file=sys.stderr)
        self.failures.append(what)

    # issues -------------------------------------------------------------------------------------------

    def milestone(self, repo: str, title: str) -> str:
        """`title` if the repository has that milestone, else "". The sync creates milestones only for rows
        it files, so a ladder entry whose rows are all done (certledger phase 0) has none, and asking GitHub
        to file an epic under it stops the run."""
        if not title:
            return ""
        lister = getattr(self.store, "milestones", None)
        if lister is None:
            return title  # a store that cannot list milestones (a test fake) is trusted
        if repo not in self._milestones:
            self._milestones[repo] = lister(repo)
        return title if title in self._milestones[repo] else ""

    def issues(self, repo: str, refresh: bool = False) -> list[Issue]:
        if refresh or repo not in self._issues:
            self._issues[repo] = self.store.read(repo)
        return self._issues[repo]

    def find(self, repo: str, marker: str, project: str = "") -> Issue | None:
        """The issue carrying `marker`, read only. With `project`, only that project's issues count."""
        return next(
            (
                i
                for i in self.issues(repo)
                if marker in i.body and (not project or owned_by(i.title, project))
            ),
            None,
        )

    def ensure(
        self,
        repo: str,
        marker: str,
        title: str,
        body: str,
        milestone: str = "",
        project: str = "",
    ) -> Issue | None:
        """The issue carrying `marker`, created or brought up to date. None in a plan run that would create it."""
        title, body = unlocal(title), unlocal(body)
        current = self.find(repo, marker, project)
        if current is None:
            if not self.say(f"create {repo} issue {title!r}"):
                return None
            self.store.create(repo, title, body, milestone)
            made = next(
                (
                    i
                    for i in self.issues(repo, refresh=True)
                    if marker in i.body and (not project or owned_by(i.title, project))
                ),
                None,
            )
            if made is None:
                raise GhProjectsError(f"created {title!r} in {repo} but cannot find it")
            return made
        stale = (
            current.title != title
            or not same(current.body, body)
            or (milestone and current.milestone != milestone)
        )
        if stale and self.say(f"update {repo}#{current.number} {title!r}"):
            self.store.edit(repo, current.number, title, body, milestone)
        return current

    # links --------------------------------------------------------------------------------------------

    def nest(self, parent: Issue, child: Issue) -> None:
        if child.node_id in self.client.linked_ids(parent.node_id, "subIssues"):
            return
        what = f"nest {_path(child.url)} under {_path(parent.url)}"
        if self.say(what):
            try:
                self.client.add_sub_issue(parent.node_id, child.node_id)
            except GhProjectsError as error:
                self.fail(f"{what}: {error}")

    def block(self, issue: Issue, blocker: Issue) -> None:
        if blocker.node_id in self.client.linked_ids(issue.node_id, "blockedBy"):
            return
        what = (
            f"mark {issue.title.split(':')[0]} blocked by {blocker.title.split(':')[0]}"
        )
        if self.say(what):
            try:
                self.client.add_blocked_by(issue.node_id, blocker.node_id)
            except GhProjectsError as error:
                self.fail(f"{what}: {error}")

    # schema -------------------------------------------------------------------------------------------

    def schema(self, write: bool = True) -> Project:
        """The board's fields, brought to the wanted schema. write False reads only: a filtered run does
        not change what every other project's cards are shown with."""
        project = self.client.project(self.owner, self.number)
        if not write:
            return project
        status = project.fields.get("Status")
        if status is not None:
            wanted = options(STATUS, status.options)
            if [o["name"] for o in wanted] != list(status.options) and self.say(
                "set Status options"
            ):
                self.client.set_options(status.id, wanted)
        for name, spec in SELECTS.items():
            if name not in project.fields and self.say(f"create field {name}"):
                self.client.create_field(
                    project.id, name, "SINGLE_SELECT", options(spec)
                )
        for name in NUMBERS:
            if name not in project.fields and self.say(f"create field {name}"):
                self.client.create_field(project.id, name, "NUMBER")
        for name in TEXT_FIELDS:
            if name not in project.fields and self.say(f"create field {name}"):
                self.client.create_field(project.id, name, "TEXT")
        for name in DATE_FIELDS:
            if name not in project.fields and self.say(f"create field {name}"):
                self.client.create_field(project.id, name, "DATE")
        if "Cycle" not in project.fields and self.say(
            f"create field Cycle (two-week, from {CYCLE_START})"
        ):
            self.client.create_field(
                project.id,
                "Cycle",
                "ITERATION",
                iteration={
                    "startDate": CYCLE_START,
                    "duration": CYCLE_DAYS,
                    "iterations": cycles(),
                },
            )
        readme, description = self.client.readme(project.id)
        if (not same(readme, README) or description != SHORT) and self.say(
            "set README and description"
        ):
            self.client.describe(project.id, SHORT, README)
        return self.client.project(self.owner, self.number) if self.apply else project

    def views(self) -> None:
        have = set(self.client.view_names(self.owner, self.number))
        ids = self.client.rest_field_ids(self.owner, self.number)
        for spec in view_specs(ids):
            if spec["name"] in have:
                continue
            body = {k: v for k, v in spec.items() if v not in ([], "", None)}
            if self.say(f"create view {spec['name']!r} ({spec['layout']})"):
                self.client.create_view(self.owner, self.number, body)

    # values -------------------------------------------------------------------------------------------

    def place(self, project: Project, wanted: dict[str, dict[str, object]]) -> None:
        """Put every wanted issue (by node id) on the board and set its fields. Status is only ever filled
        when empty, except Done, which a done row earns. Also reports the board-ahead and Active drift."""
        items = {i.content_id: i for i in self.client.items(self.owner, self.number)}
        missing = [node for node in wanted if node not in items]
        if missing and self.say(f"add {len(missing)} issue(s) to the board"):
            self.client.add_items(project.id, missing)
            items = {
                i.content_id: i for i in self.client.items(self.owner, self.number)
            }
        for what in ahead_for(items, wanted):
            self.note(BOARD_AHEAD, what)
        active = active_items(items.values())
        if len(active) > ACTIVE_LIMIT:
            self.note(
                "active",
                f"{len(active)} cards are Active, the rule is at most {ACTIVE_LIMIT}: "
                + ", ".join(sorted(active)),
            )
        changes = changes_for(project, items, wanted)
        if changes and self.say(f"set {len(changes)} field value(s)"):
            for outcome in self.client.set_values(project, changes):
                if not outcome.ok:
                    self.fail(f"{outcome.change}: {outcome.error}")


def _path(url: str) -> str:
    return url.split("github.com/")[-1]


def changes_for(
    project: Project, items: dict[str, Item], wanted: dict[str, dict[str, object]]
) -> list[Change]:
    changes: list[Change] = []
    for node, values in wanted.items():
        item = items.get(node)
        if item is None:
            continue
        for field, value in values.items():
            if field not in project.fields or value in ("", None):
                continue
            if field == "Status" and value != "Done" and item.values.get("Status"):
                continue  # never move a card a person moved
            current = item.values.get(field)
            if (
                isinstance(value, int | float)
                and isinstance(current, int | float)
                and float(current) == float(value)
            ):
                continue
            if current != value:
                changes.append(Change(item.id, field, value))
    return changes


def ahead_for(
    items: dict[str, Item], wanted: dict[str, dict[str, object]]
) -> list[str]:
    """Obligation cards the board shows Done while their ROADMAP row is not done. No write cures this:
    the row is the authority, and a card cannot make it done."""
    out = []
    for node, values in wanted.items():
        item = items.get(node)
        if (
            item is not None
            and values.get("Level") == "Obligation"
            and values.get("Status") != "Done"
            and item.values.get("Status") == "Done"
        ):
            out.append(
                f"{_path(item.url) or item.title} is Done on the board, not in its ROADMAP"
            )
    return out


def active_items(items: Iterable[Item]) -> list[str]:
    return [
        _path(i.url) or i.title for i in items if i.values.get("Active") == "Active"
    ]


def row_values(
    row: dict[str, str], tier: int, wave: int, node: str = ""
) -> dict[str, object]:
    """The board fields one obligation row owns.

    `node` is a pytest node id computed by the caller through `roadmap.named_test`, never the Where cell. The
    first version wrote `row["where"]` here, which put a whole evidence paragraph into a Projects text column:
    GitHub rejected every one of trustatlas's long rows with "Column value must be a valid value for text
    column", so `--apply` failed its writes and `--check` reported the same six differences forever. A board
    field is a pointer to evidence, not a copy of it.
    """
    done = STATUSES.get(row["status"], ("", "open"))[1] == "close"
    return {
        "Level": "Obligation",
        "Spec": row["spec"],
        "Named test": node,
        "Priority": priority(tier),
        "Wave": f"Wave {wave}" if wave else "",
        "Status": "Done" if done else "Backlog",
    }


def row_done(row: dict[str, str]) -> bool:
    return STATUSES.get(row["status"], ("", "open"))[1] == "close"


# -- the build --------------------------------------------------------------------------------------------


def build(board: Board, only: set[str] | None) -> None:
    """Project the files onto the board. With `only`, touch nothing but those projects: their own card, its
    links, their milestone epics and rows. Waves, decisions, the schema, the views and every other project's
    card are read (a link needs the issue at its other end) and never written."""
    text = (ROOT / "PORTFOLIO.md").read_text(encoding="utf-8")
    entries = portfolio.entries()
    names = {e.name for e in entries}
    if only is not None and only - names:
        raise GhProjectsError(
            f"--project names no portfolio project: {', '.join(sorted(only - names))}"
        )
    full = only is None

    def in_scope(name: str) -> bool:
        return full or name in (only or set())

    states = {e.name: portfolio.state(ROOT, e) for e in entries}
    project = board.schema(write=full)
    wanted: dict[str, dict[str, object]] = {}

    print("decisions:")
    every = portfolio.decisions(text, names)
    decisions = [d for d in every if not d.decided]
    decision_issues: dict[int, Issue] = {}
    for d in decisions:
        marker = MARK.format("decision", d.number)
        issue = (
            board.ensure(PORTFOLIO_REPO, marker, decision_title(d), decision_body(d))
            if full
            else board.find(PORTFOLIO_REPO, marker)
        )
        if issue:
            decision_issues[d.number] = issue
            if full:
                wanted[issue.node_id] = {"Level": "Decision", "Status": "Backlog"}
                if issue.state != "OPEN":
                    board.note(
                        BOARD_AHEAD,
                        f"{_path(issue.url)} (decision {d.number}) is closed; PORTFOLIO.md section 9 "
                        "does not record it as decided",
                    )
    if full:
        for d in every:
            found = board.find(PORTFOLIO_REPO, MARK.format("decision", d.number))
            if d.decided and found is not None and found.state == "OPEN":
                board.note(
                    FILE_AHEAD,
                    f"{_path(found.url)}: decision {d.number} is decided in PORTFOLIO.md; close the issue",
                )

    print("projects:")
    project_issues: dict[str, Issue] = {}
    rows_by: dict[str, list[dict[str, str]]] = {}
    links_by: dict[str, dict[str, Issue]] = {}
    for entry in entries:
        state = states[entry.name]
        marker = MARK.format("project", entry.name)
        if not in_scope(entry.name):
            # Read only: the other end of a blocked-by link. Its body is not rebuilt, because rebuilding it
            # without its row issues would strip every row link from it.
            found = board.find(PORTFOLIO_REPO, marker)
            if found is not None:
                project_issues[entry.name] = found
            rows_by[entry.name], links_by[entry.name] = [], {}
            continue
        rows = (
            rows_of((ROOT / entry.directory / state.matrix).read_text(encoding="utf-8"))
            if state.matrix
            else []
        )
        rows_by[entry.name] = rows
        row_issues: dict[str, Issue] = {}
        if state.repo and rows:
            for issue in board.issues(issue_home(state.repo)):
                if not owned_by(issue.title, entry.name):
                    continue
                found_row = ROW_MARKER.search(issue.body)
                if found_row:
                    row_issues[found_row.group(1)] = issue
        links_by[entry.name] = row_issues
        depends = [
            (d, states[d].label if d in states else "Absent") for d in entry.depends
        ]
        gates = [
            (d.number, d.title.replace("`", ""))
            for d in decisions
            if entry.name in d.projects
        ]
        body = project_body(
            entry,
            state,
            depends,
            gates,
            rows,
            {k: v.url for k, v in row_issues.items()},
        )
        issue = board.ensure(PORTFOLIO_REPO, marker, project_title(entry, state), body)
        if issue is None:
            continue
        project_issues[entry.name] = issue
        wanted[issue.node_id] = {
            "Level": "Project",
            "Kind": entry.kind,
            "State": state.label,
            "Priority": priority(entry.tier),
            "Wave": f"Wave {entry.wave}" if entry.wave else "",
            "Rows done": state.done if state.rows else None,
            "Rows open": state.rows - state.done if state.rows else None,
            "Status": "Backlog",
        }

    print("waves:")
    wave_list = portfolio.waves(text)
    for wave in wave_list:
        members = [
            (e, states[e.name]) for e in entries if e.wave == wave.number and e.tier
        ]
        marker = MARK.format("wave", wave.number)
        issue = (
            board.ensure(
                PORTFOLIO_REPO, marker, wave_title(wave), wave_body(wave, members)
            )
            if full
            else board.find(PORTFOLIO_REPO, marker)
        )
        if issue is None:
            continue
        if full:
            wanted[issue.node_id] = {
                "Level": "Wave",
                "Wave": f"Wave {wave.number}",
                "Status": "Backlog",
            }
        for entry, _ in members:
            if entry.name in project_issues and in_scope(entry.name):
                board.nest(issue, project_issues[entry.name])

    if full:
        expected = {MARK.format("project", n) for n in names}
        expected |= {MARK.format("wave", w.number) for w in wave_list}
        expected |= {MARK.format("decision", d.number) for d in every}
        for issue in board.issues(PORTFOLIO_REPO):
            found_mark = PORTFOLIO_MARKER.search(issue.body)
            if (
                found_mark
                and found_mark.group(0) not in expected
                and issue.state == "OPEN"
            ):
                board.note(
                    BOARD_AHEAD,
                    f"{_path(issue.url)} carries {found_mark.group(0)}, which the files no longer name",
                )

    print("links:")
    for entry in entries:
        issue = project_issues.get(entry.name)
        if issue is None or not in_scope(entry.name):
            continue
        for dep in entry.depends:
            if dep in project_issues and states[dep].label != "Implemented":
                board.block(issue, project_issues[dep])
        for d in decisions:
            if entry.name in d.projects and d.number in decision_issues:
                board.block(issue, decision_issues[d.number])

    print("milestones and rows:")
    for entry in entries:
        state, rows, row_issues = (
            states[entry.name],
            rows_by[entry.name],
            links_by[entry.name],
        )
        parent = project_issues.get(entry.name)
        if not (in_scope(entry.name) and state.repo and rows and row_issues and parent):
            continue
        # The same ladder the sync reads, so an epic never names a milestone the sync did not create (a
        # certledger "phase 0" epic once asked GitHub for a milestone that did not exist and stopped the run).
        text = (ROOT / entry.directory / state.matrix).read_text(encoding="utf-8")
        ladder = (
            roadmap.ladder_for(ROOT, entry.name)
            if entry.name in roadmap.LADDERS
            else roadmap.ladder_of(text, "M")
        )
        numbers = {k: v.number for k, v in row_issues.items()}
        for row in rows:
            issue = row_issues.get(row["name"])
            if issue is None:
                continue
            if issue.state == "OPEN" or row_done(row):
                wanted[issue.node_id] = row_values(
                    row,
                    entry.tier,
                    entry.wave,
                    roadmap.named_test(
                        ROOT, entry.name, row["where"], row.get("evidence", "")
                    )[0],
                )
            if issue.state != "OPEN" and not row_done(row):
                board.note(
                    BOARD_AHEAD,
                    f"{_path(issue.url)} is closed; {entry.directory}/{state.matrix} has "
                    f"{row['title']} at {row['status']}",
                )
        named = {r["name"] for r in rows}
        for key, issue in sorted(row_issues.items()):
            if key not in named and issue.state == "OPEN":
                board.note(
                    BOARD_AHEAD,
                    f"{_path(issue.url)} is open for row {key}, which "
                    f"{entry.directory}/{state.matrix} no longer has",
                )
        for key in sorted({r["milestone"] for r in rows if r["milestone"]}):
            in_milestone = [r for r in rows if r["milestone"] == key]
            epic = board.ensure(
                issue_home(state.repo),
                EPIC_MARKER.format(key),
                epic_title(entry.name, key, ladder),
                epic_body(entry.name, state.repo, key, ladder, in_milestone, numbers),
                ""
                if shared()
                else board.milestone(
                    state.repo,
                    rowdoc.milestone_title(key, ladder) if key in ladder else "",
                ),
                project=entry.name,
            )
            if epic is None:
                continue
            board.nest(parent, epic)
            wanted[epic.node_id] = {
                "Level": "Milestone",
                "Priority": priority(entry.tier),
                "Wave": f"Wave {entry.wave}" if entry.wave else "",
                "Status": "Backlog",
            }
            for row in in_milestone:
                if row["name"] in row_issues:
                    board.nest(epic, row_issues[row["name"]])
        for row in rows:
            if (
                not row["milestone"]
                and row["name"] in row_issues
                and row_issues[row["name"]].state == "OPEN"
            ):
                board.nest(parent, row_issues[row["name"]])

    print("board:")
    board.place(project, wanted)
    if full:
        board.views()


def report(board: Board) -> None:
    if not board.drift:
        print("check: the board matches the files")
        return
    print(f"check: {len(board.drift)} difference(s)")
    for kind in (FILE_AHEAD, BOARD_AHEAD, "active"):
        found = [what for k, what in board.drift if k == kind]
        if found:
            print(f"  {kind} ({len(found)}):")
            print("\n".join(f"    {what}" for what in found))


def main(
    argv: Sequence[str] | None = None,
    client: Projects | None = None,
    issues: Issues | None = None,
) -> int:
    """The CLI. `client` and `issues` are injected by the tests, which never reach the network."""
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--number",
        type=int,
        default=BOARD_NUMBER,
        help=f"the board (default {BOARD_NUMBER}, from FORGE_BOARD)",
    )
    target.add_argument(
        "--create",
        metavar="TITLE",
        help="create a board with this title (with --apply); refused when any board has the title",
    )
    parser.add_argument("--owner", default=OWNER)
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="only the Project's fields and views: needs no workspace, so a workflow can run it",
    )
    parser.add_argument(
        "--project",
        action="append",
        help="touch only these projects: their card, epics and rows",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument(
        "--check",
        action="store_true",
        help="read only; exit 1 on file-ahead or board-ahead drift, or more than two Active cards",
    )
    args = parser.parse_args(argv)
    client = client if client is not None else Projects()
    try:
        client.guard(needed=500)
        number = args.number
        if args.create:
            if not args.apply:
                clash = [
                    p
                    for p in client.list_projects(args.owner)
                    if p.title == args.create
                ]
                if clash:
                    print(
                        f"board: {args.owner} already has {args.create!r} as #{clash[0].number}; "
                        "a create would be refused",
                        file=sys.stderr,
                    )
                    return 1
                print(
                    f"would create board {args.create!r}; a plan run against a board needs --number"
                )
                return 0
            made = client.create_project(args.owner, args.create)
            number = made.number
            print(f"created board #{number}")
        board = Board(client, args.owner, number, args.apply, issues)
        if args.schema_only:
            board.schema(write=True)
            board.views()
        else:
            build(board, set(args.project) if args.project else None)
    except GhProjectsError as error:
        print(f"board: {error}", file=sys.stderr)
        return 1
    if board.failures:
        print(f"board: {len(board.failures)} write(s) failed", file=sys.stderr)
        return 1
    if args.check:
        report(board)
        return 1 if board.drift else 0
    if not args.apply:
        print("plan only: nothing changed. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
