"""Pick the obligation the portfolio can start right now, and name the command that runs it.

Why a script decides this. Eligibility is arithmetic: which projects a person marked Active, which rows the
board marks Ready, whether the two-project rule in PORTFOLIO.md section 7 leaves a slot, whether a dependency
exists as code. None of it needs a model, and planner.py's own note about Task Master's 36 tools (about 21,000
tokens of context) is the reason not to spend tokens on it: a tool list is context not spent on the code.

Why the board is read through projects_mcp.py. It is the one read surface that already understands Level,
State, Active and the active rule, and its views are pure over items, so this file is testable with invented
cards and no token at all. Reading GraphQL here instead would give the portfolio two ideas of what "Ready"
means, which is the drift this forge exists to prevent. The GitHub MCP server is not the answer for this
question: its `projects` toolset returns titles unless field ids are passed, and it is skipped entirely under
`--read-only`, which is how the `github` server is configured on this machine.

Why nothing here writes. Decision D23: a card is a claim, and a row reaches `[ok]` only in the same commit as a
named test that would fail if the behaviour regressed. So this emits a step and a command; the flip, the gates
and sync_roadmap.py stay in the CLIs that check evidence. Two fields make the boundary concrete: `promote`
names the human action when a project is not yet Active (the board's own readme says Active is a person's
field), and `caveats` carries deps.py's refusal to call a dependency unblocked.

Usage:
    py loop.py next --json
    py loop.py next --project pkix
    py loop.py next --explain
"""

from __future__ import annotations

from projecthub.github.sync import shared

import argparse
import json
import os
import pathlib
import re
from datetime import UTC, datetime
from collections.abc import Callable, Collection, Sequence
from typing import Any, cast

from projecthub import deps
from projecthub.mcp import read as planner
from projecthub.github import projects_mcp as views
from projecthub.db import parser as roadmap
from projecthub.github.client import Item, Projects

ROOT = pathlib.Path(os.environ.get("FORGE_ROOT") or "D:/code")
OWNER = os.environ.get("LOOP_OWNER") or views.DEFAULT_OWNER
BOARD = int(os.environ.get("LOOP_BOARD") or views.DEFAULT_NUMBER)

# The Status options as they exist on board b1tninja#4, read from the live API on 2026-11-07: Backlog, Ready,
# In progress, In review, Blocked, Done. Anything outside this set is a bug in this file, not a board state.
CONTINUABLE = "In progress"
STARTABLE = "Ready"
BACKLOG = "Backlog"
DONEISH = ("Done", "In review")
RANK = {CONTINUABLE: 0, STARTABLE: 1, BACKLOG: 2}
# deps.py reports a dependency's state; it refuses to say a dependent is unblocked, and neither does this.
HARD_BLOCK = ("absent", "plan only")
SOFT_BLOCK = ("shell",)


def repo_of(card: Item) -> str:
    """`b1tninja/pkix` from the card's own url.

    The Repository built-in is left blank often enough on projection that a url is the reliable source, and an
    empty answer here is reported rather than guessed at.
    """
    parts = [p for p in card.url.split("/") if p]
    return f"{parts[2]}/{parts[3]}" if len(parts) > 4 else ""


def project_of(card: Item) -> str:
    """The `[name]` title prefix, which says the project now that every issue lives in one repository; the
    repository name for a card without one."""
    if shared():
        return card_project(card) or repo_of(card).split("/")[-1]
    return repo_of(card).split("/")[-1]


def plain(name: str) -> str:
    """A row name as both sides of the join write it: a key, never an identity to print.

    The board titles a pkix card "[pkix] anchors.load_anchor(): ..." while planner.next_actions returns the
    same obligation as "anchors.load_anchor", because it strips the call marker when it parses the matrix.
    Joining on the raw strings reported a real row as drift and dropped the named test that belongs to it -
    measured on the live board on 2026-11-07, where the row is `anchors.load_anchor()` at ROADMAP.md line 21
    with `tests/test_obligations.py::load_anchor` beside it. Both sides come through here, so both lose the
    backticks and the trailing (). Nothing a person reads is built from this: the row id that loop prints and
    puts in the prompt keeps its call marker, because that is what the matrix file literally contains.
    """
    cleaned = name.replace("`", "").strip()
    return cleaned[:-2] if cleaned.endswith("()") else cleaned


def title_row(title: str) -> str:
    """A card title read as a row id when it carries no `[project]` prefix, keeping the call marker."""
    return title.replace("`", "").strip()


# What sync_roadmap.py and board.py actually title an obligation card, read off the live board on
# 2026-11-07: "[certledger] cl.multi-path: Model I7", and "[pkix] anchors.load_anchor(): trust anchor
# selection, including name restrictions and ...". The matrix calls those rows "cl.multi-path" and
# "anchors.load_anchor()". Two things the first shape did not teach: a row id carries parentheses, and the
# prose after the colon is truncated with an ellipsis by the projector. Both came from running the selector
# against the live board rather than against the fixtures, and the second is harmless because spec is a
# fallback that the matrix cell outranks.
CARD = re.compile(
    r"^\[(?P<project>[^\]]+)\]\s+(?P<row>[\w.@/\[\]()+-]+)\s*(?::\s*(?P<spec>.*))?$"
)


def card_row(card: Item) -> str:
    """The row id inside a card title, or the stripped title when the card is not in that shape.

    A card that does not match is not guessed at: it is matched by title later, and _step reports the
    disagreement, because a wrong join here would send a run to a different row than the one a person marked.
    """
    match = CARD.match(card.title.strip())
    return match.group("row") if match else title_row(card.title)


def card_spec(card: Item) -> str:
    """The spec the title carries, which is a copy of the matrix cell and used only as a fallback."""
    match = CARD.match(card.title.strip())
    return (match.group("spec") or "").strip() if match else ""


def card_project(card: Item) -> str:
    """The project inside the card's `[name]` prefix, or "" when the title has none."""
    match = CARD.match(card.title.strip())
    return match.group("project").strip() if match else ""


def folder_of(root: pathlib.Path, name: str) -> str:
    """The directory a project name resolves to, so `itulib` and its `itulibrary` repository can be compared."""
    return roadmap.project_dir(root, name).name.lower()


def startable(card: Item, active: list[str]) -> bool:
    """Whether this card may be taken, given who is Active.

    board.py leaves every row at Backlog and states that it never moves Status forward: Ready, In progress,
    In review and Blocked are a person's fields. So inside a project somebody already committed to, Backlog is
    a genuine offer of work; outside one, only an explicit Ready counts. Without that second half the loop
    would open a third front the two-project rule forbids, on the strength of a status nobody chose.
    """
    status = str(card.values.get("Status", ""))
    if status not in RANK:
        return False
    if status in (CONTINUABLE, STARTABLE):
        return True
    return project_of(card) in active


def active_projects(cards: Sequence[Item]) -> list[str]:
    """Projects a person marked Active.

    Names come from the card title, which board.py builds as `name: prose`; lower-cased, because a repository's
    case is not stable across a rename and the comparison below is about identity, not spelling.
    """
    return [
        card.title.split(":")[0].strip().lower()
        for card in views.select(cards, {"Level": "Project"})
        if card.values.get("Active") == "Active"
    ]


def open_decision_hits(cards: Sequence[Item], project: str) -> list[str]:
    """Open section 9 decisions naming this project, which is how board.py wires a block."""
    needle = project.lower()
    # overview() builds a payload for the MCP tool and is typed dict[str, object]; open_decisions is a list of
    # strings there. The cast says that here rather than loosening a signature that another caller renders.
    decisions = cast("list[str]", views.overview(cards)["open_decisions"])
    return [d for d in decisions if needle in d.lower()]


def blockings(root: pathlib.Path, project: str) -> tuple[list[str], list[str]]:
    """`(hard, notes)` for one project's dependencies.

    A dependency that is absent or a plan is withheld: the plan's own sentence ("does not exist yet") means
    blocked, and the workspace rule says a dependent must not start. A dependency that is a shell is reported
    and allowed, because whether a package that exists provides the capability an edge needs is a per-edge
    question that deps.py refuses to answer from a file count, and this file has no better right to it.
    """
    hard: list[str] = []
    notes: list[str] = []
    for edge in deps.edges(root, project):
        state = deps.dependency_state(root, edge.dependency).state
        if state in HARD_BLOCK:
            hard.append(edge.dependency)
            notes.append(f"hard dependency {edge.dependency} is {state} on disk")
        elif state in SOFT_BLOCK:
            notes.append(
                f"hard dependency {edge.dependency} is a shell: it exists and does nothing yet"
            )
    return hard, notes


def dependency_notes(root: pathlib.Path, project: str) -> list[str]:
    """The prose half of blockings(), for a caller that only reports."""
    return blockings(root, project)[1]


RUNBOOK = ".cline/workflows/implement-obligation.md"


def runbook_gap(root: pathlib.Path, project: str) -> str:
    """Why this project cannot be run headless, or `""` when it can.

    The command emitted here is a slash command, and a slash command only resolves against the project's own
    `.cline/workflows`. Measured on 2026-11-07: all 29 generated shells carry `implement-obligation.md` and
    none of the phase-1 projects do, so the selector chose `asn1core` and the run died on "Unknown command or
    unquoted prompt" without reading a line of code. A row that no process can execute is not the next step,
    and the reason belongs in the output next to the row that can be.
    """
    if (roadmap.project_dir(root, project) / RUNBOOK).is_file():
        return ""
    return (
        f"{RUNBOOK} is absent from {project}, so /implement-obligation has nothing to expand: "
        "port the runbook or run the row by hand"
    )


def command_for(row: str, cwd: pathlib.Path, *, timeout: int = 1800) -> str:
    """The run itself: one row, one process, in its own worktree.

    `--worktree` because this workspace carries more than one agent at a time, and a detached worktree under
    ~/.cline/worktrees is what keeps a loop out of a tree someone else is mid-commit in. A fresh process per
    row because of cline-memory-leak-report.md, where one long session reached 62 GB and 9,195 messages; the
    workflow body stays short for the same reason. `--retries` and `--timeout` bound the damage, and `--json`
    leaves a run_result line carrying finishReason and usage, which is the only honest telemetry to log. Mode
    is act with auto-approve, stated rather than assumed, because the schedule subcommand defaults to yolo.

    This string is quoted for a POSIX shell. Passing it through a second PowerShell layer eats the inner quotes
    and the prompt arrives as two arguments, which is how a run failed on 2026-11-07; the step also carries
    `prompt` as one field so a driver can hand it over as a single argument and never parse quotes.
    """
    return (
        f'cline -c "{cwd.as_posix()}" --worktree --auto-approve true '
        f"--timeout {timeout} --retries 3 --json "
        f'"/implement-obligation {row}"'
    )


def pick(
    root: pathlib.Path,
    cards: Sequence[Item],
    prefer: str = "",
    *,
    file_rows: Callable[[str], list[dict[str, str]]] | None = None,
    matrix_for: Callable[[str], pathlib.Path] | None = None,
    runbook: Callable[[str], str] | None = None,
    leased: Collection[str] = (),
) -> dict[str, Any]:
    """The one step to take, or a stop reason. Pure over `cards`, so a test can invent a board.

    Ordering is the whole policy: continue a row already In progress, then start a Ready row inside a project
    somebody marked Active, and only then reach outside the active two - and even then it is proposed, never
    taken, because the board's own readme reserves Active for a person. The file is the authority on a row and
    the board is the authority on intent, so both are read and a disagreement is reported as a caveat rather
    than quietly preferring one.
    """
    rows_of_file = file_rows or (lambda name: planner.next_actions(name, limit=500))
    matrix_path = matrix_for or planner.roadmap_path

    active = active_projects(cards)
    candidates = [
        card
        for card in views.select(cards, {"Level": "Obligation"})
        if project_of(card) and startable(card, active)
    ]
    candidates.sort(
        key=lambda c: (
            RANK[str(c.values.get("Status"))],
            project_of(c) not in active,
            project_of(c),
            card_row(c),
        )
    )
    if prefer:
        needle = prefer.lower()
        candidates.sort(key=lambda c: 0 if project_of(c) == needle else 1)

    skipped: list[dict[str, str]] = []
    for card in candidates:
        project = project_of(card)
        if claim_key(project, card_row(card)) in leased:
            skipped.append(
                {
                    "project": project,
                    "row": card_row(card),
                    "why": "another agent holds the claim on this row",
                }
            )
            continue
        hard, notes = blockings(root, project)
        decisions = open_decision_hits(cards, project)
        # A project is blocked by any open section 9 decision that names it, which is how board.py wires the
        # edge. So that is a withheld reason here, not a footnote: the decision is the thing a person has to
        # settle before a row in that project can honestly be called startable.
        if decisions:
            notes.append("open decision names this project: " + "; ".join(decisions))
        if hard or decisions:
            skipped.append(
                {"project": project, "row": card_row(card), "why": "; ".join(notes)}
            )
            continue
        # A row inside a project with no runbook is not the next step, it is a command that will fail. The
        # check is injected rather than always on so that pick() stays pure over cards for the tests, and
        # main() never omits it: the failure it prevents was measured, not imagined.
        if runbook is not None:
            gap = runbook(project)
            if gap:
                notes.append(gap)
                skipped.append(
                    {"project": project, "row": card_row(card), "why": "; ".join(notes)}
                )
                continue
        if project not in active and len(active) >= views.ACTIVE_LIMIT:
            skipped.append(
                {
                    "project": project,
                    "row": card_row(card),
                    "why": f"both slots are used ({', '.join(active)}) and only a person sets Active",
                }
            )
            continue
        return _step(
            root,
            card,
            project,
            active=active,
            caveats=notes,
            promote=project not in active,
            rows_of_file=rows_of_file,
            matrix_path=matrix_path,
            skipped=skipped,
        )

    return _stop(cards, active, skipped)


def _step(
    root: pathlib.Path,
    card: Item,
    project: str,
    *,
    active: list[str],
    caveats: list[str],
    promote: bool,
    rows_of_file: Callable[[str], list[dict[str, str]]],
    matrix_path: Callable[[str], pathlib.Path],
    skipped: list[dict[str, str]],
) -> dict[str, Any]:
    name = card_row(card)
    open_rows = {plain(r["row"]): r for r in rows_of_file(project)}
    row = open_rows.get(plain(name))
    matrix = matrix_path(project)
    caveats = list(caveats)
    if row is None:
        caveats.append(
            f"the board titles this card {card.title!r} and joins it to row {name!r}, but {matrix.name} has "
            "no open row of that name: one of the two is stale, and sync_roadmap --check would call that drift"
        )
    named = card_project(card)
    if named and folder_of(root, named) != project:
        caveats.append(
            f"the card is titled [{named}] but its issue lives in {repo_of(card)}: the projection disagrees "
            "with itself about which project owns this row"
        )
    cwd = roadmap.project_dir(root, project)
    return {
        "project": project,
        "row": name,
        "board_status": str(card.values.get("Status", "")),
        "file_status": (row or {}).get("status", ""),
        "spec": (row or {}).get("spec")
        or str(card.values.get("Spec", ""))
        or card_spec(card),
        "milestone": (row or {}).get("milestone")
        or str(card.values.get("Milestone", "")),
        "named_test": (row or {}).get("named_test")
        or str(card.values.get("Named test", "")),
        "repository": repo_of(card),
        "cwd": str(cwd),
        "matrix": str(matrix),
        "board_url": card.url,
        "active_projects": active,
        "active_rule": f"{len(active)} of {views.ACTIVE_LIMIT} slots used",
        "promote": promote,
        "needs": "a person sets Active on this project: the board reserves that field"
        if promote
        else "",
        "caveats": caveats,
        "skipped": skipped,
        "prompt": f"/implement-obligation {name}",
        "command": command_for(name, cwd),
        "source": f"board {OWNER}#{BOARD} via projects_mcp views, and {project}/{matrix.name}",
    }


def _stop(
    cards: Sequence[Item], active: list[str], skipped: list[dict[str, str]]
) -> dict[str, Any]:
    """Why nothing may start, in the terms the rule uses. A driver treats this as "exit, do not retry"."""
    overview = views.overview(cards)
    ready = [
        card_row(card)
        for card in views.select(cards, {"Level": "Obligation", "Status": STARTABLE})
    ]
    backlog = [
        card_row(card)
        for card in views.select(cards, {"Level": "Obligation", "Status": BACKLOG})
    ]
    if not active and backlog:
        reason = (
            f"nothing is Active: {len(backlog)} rows sit at Backlog and board.py will not move Status, so a "
            f"person has to mark one or two projects Active on board {OWNER}#{BOARD}"
        )
    elif len(active) >= views.ACTIVE_LIMIT and not ready:
        reason = (
            f"both slots ({', '.join(active)}) are used and no card is Ready: nothing may start until a row "
            "finishes or a person retires one of them"
        )
    elif skipped and ready:
        reason = "every Ready card is withheld by a dependency, a decision or the active rule; see skipped"
    elif skipped:
        reason = "the only cards in play are withheld; see skipped"
    else:
        reason = "no card is Ready or In progress, and no Backlog row sits inside a project marked Active"
    return {
        "stop": reason,
        "active_projects": active,
        "active_rule": overview["active_rule"],
        "ready_cards": ready[:20],
        "backlog_rows": len(backlog),
        "skipped": skipped,
        "source": f"board {OWNER}#{BOARD} via projects_mcp views",
    }


# ------------------------------------------------------------------------------------------ pickup

LEASE_COMMAND = "projecthub leases"
NO_WORK_EXIT = 3
PICK_ATTEMPTS = 25


def claim_key(project: str, row: str) -> str:
    """The lease key for a board row: `project:row`, with the backticks and call parentheses taken off."""
    return f"{project}:{row.strip('`').removesuffix('()')}"


def _no_work(reason: str, skipped: Sequence[dict[str, str]] = ()) -> str:
    """Its own state, because a model with nothing to do invents a task."""
    lines = [
        f"NO WORK: {reason}",
        "",
        "Ask the person what to work on. The board decides the order of work.",
    ]
    lines += [
        f"  withheld {row['project']}/{row['row']}: {row['why']}"
        for row in skipped[:10]
    ]
    return "\n".join(lines) + "\n"


def _bullets(title: str, items: Sequence[str]) -> list[str]:
    return [f"## {title}", "", *[f"- {item}" for item in items], ""] if items else []


def assignment(step: dict[str, Any], packet: dict[str, Any], lease: Any) -> str:
    """The one-item contract: everything needed for this row, and the one way to end it."""
    stub = packet.get("stub") or {}
    acceptance = packet.get("acceptance") or {}
    expires = datetime.fromtimestamp(lease.expires_at, UTC).isoformat(
        timespec="minutes"
    )
    finish = f"{LEASE_COMMAND} {{}} {lease.lease_id}"
    lines = [
        f"# Your assignment: {packet['row_id']}",
        "",
        f"You hold the claim on this row until {expires}. The board chose it, so work only this row.",
        "",
        f"- Work in: {step['cwd']} (read its AGENTS.md first)",
        f"- Status: {packet.get('status') or step.get('board_status')}",
        f"- Where: {packet.get('where') or '-'}",
    ]
    if stub:
        lines.append(f"- Implements: {stub.get('path')}: {stub.get('signature')}")
    lines += [
        f"- Named test: {packet.get('named_test') or '-'} ({packet.get('test_state')})",
        f"- Spec: {packet.get('spec') or '-'}. {packet.get('spec_note') or ''}",
    ]
    if packet.get("milestone"):
        lines.append(
            f"- Milestone: {packet['milestone']} {packet.get('milestone_text') or ''}".rstrip()
        )
    lines.append("")
    lines += _bullets(
        "Finished means",
        [acceptance[k] for k in ("done_when", "test", "gates") if acceptance.get(k)],
    )
    lines += _bullets(
        "Depends on",
        [f"{d['project']}: {d['state']}" for d in packet.get("depends_on") or []],
    )
    lines += _bullets(
        "Open decisions naming this project", list(packet.get("open_decisions") or [])
    )
    lines += _bullets("Notes on this row", list(packet.get("notes") or []))
    lines += _bullets("Caveats", list(step.get("caveats") or []))
    lines += [
        "## End this work with one of these",
        "",
        "```",
        f"{finish.format('release')} pr_opened --pr-url URL",
        f'{finish.format("release")} handoff --note "what is done, what is next, what to watch for"',
        f'{finish.format("release")} abandoned --note "why"',
        f'{finish.format("block")} dependency|spec_question|decision_needed|env "what you need"',
        "```",
        "",
        f"If the work runs long, renew the claim: `{finish.format('heartbeat')}`.",
        "If a decision belongs to the person, block with decision_needed, name the options, and stop.",
        "Say done, fixed or tested only when a tool's output shows it.",
        "",
    ]
    return "\n".join(lines)


def run_pickup(
    root: pathlib.Path,
    cards: Sequence[Item],
    *,
    store: Any,
    make_packet: Callable[[str], dict[str, Any]],
    prefer: str = "",
    who: str = "agent",
    file_rows: Callable[[str], list[dict[str, str]]] | None = None,
    matrix_for: Callable[[str], pathlib.Path] | None = None,
) -> tuple[int, str]:
    """Pick one row, claim it and return the assignment, or a distinct NO WORK state with its reason.

    The runbook check is left out on purpose: it guards a Cline slash command, and this output runs in any client.
    A row that cannot produce a packet is passed over, so one stale card cannot stop every arrival.
    """
    from projecthub.mcp import work as lease_store

    excluded = set(store.live_keys()) | set(store.blocked_keys())
    for _ in range(PICK_ATTEMPTS):
        step = pick(
            root,
            cards,
            prefer=prefer,
            file_rows=file_rows,
            matrix_for=matrix_for,
            leased=excluded,
        )
        if "command" not in step:
            return NO_WORK_EXIT, _no_work(str(step["stop"]), step.get("skipped") or [])
        if step["needs"]:
            return NO_WORK_EXIT, _no_work(
                f"{step['project']}/{step['row']}: {step['needs']}", step["skipped"]
            )
        key = claim_key(step["project"], step["row"])
        try:
            packet = make_packet(key)
        except LookupError:
            excluded.add(key)
            continue
        project, _, row_id = key.partition(":")
        try:
            lease = store.acquire(project, row_id, who)
        except lease_store.LeaseError as error:
            if error.code != "LEASE_HELD":
                raise
            excluded.add(key)
            continue
        return 0, assignment(step, packet, lease)
    return NO_WORK_EXIT, _no_work(
        f"no row could be claimed in {PICK_ATTEMPTS} attempts"
    )


def pickup_main(args: argparse.Namespace, cards: Sequence[Item]) -> int:
    from projecthub.mcp import work as lease_store

    store = lease_store.Store()
    code, text = run_pickup(
        ROOT,
        cards,
        store=store,
        make_packet=lambda key: planner.packet(key, leases=None),
        prefer=args.project,
        who=args.who,
    )
    print(text, end="")
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="loop.py",
        description="Name the next obligation the portfolio can start, and the command that runs it.",
    )
    parser.add_argument("action", nargs="?", default="next", choices=("next", "pickup"))
    parser.add_argument("--project", default="", help="consider only this project")
    parser.add_argument(
        "--json", action="store_true", help="machine-readable step for a driver"
    )
    parser.add_argument("--who", default="agent", help="pickup: who holds the claim")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        help="pickup: also write the assignment to this file",
    )
    parser.add_argument("--owner", default=OWNER, help="Projects board owner")
    parser.add_argument(
        "--board", type=int, default=BOARD, help="Projects board number"
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="how many withheld rows to show"
    )
    args = parser.parse_args(argv)

    try:
        cards = Projects().items(args.owner, args.board)
    except Exception as error:  # noqa: BLE001 - ghprojects raises typed errors that already name the fix
        print(json.dumps({"error": str(error)}) if args.json else f"error: {error}")
        return 1

    if args.action == "pickup":
        return pickup_main(args, cards)

    step = pick(
        ROOT,
        cards,
        prefer=args.project,
        runbook=lambda name: runbook_gap(ROOT, name),
    )
    if args.json:
        print(json.dumps(step, indent=2, sort_keys=True))
    elif "stop" in step:
        print(step["stop"])
        print(
            f"  active: {', '.join(step['active_projects']) or '(none)'}   {step['active_rule']}"
        )
        for row in step["skipped"][: args.limit]:
            print(f"  withheld {row['project']}/{row['row']}: {row['why']}")
    else:
        print(
            f"{step['project']}  {step['row']}  [{step['file_status'] or step['board_status']}]"
        )
        print(f"  spec      {step['spec']}")
        print(
            f"  milestone {step['milestone']}   named test {step['named_test'] or '(none yet)'}"
        )
        print(
            f"  active    {step['active_rule']}: {', '.join(step['active_projects']) or '(none)'}"
        )
        if step["needs"]:
            print(f"  needs     {step['needs']}")
        for note in step["caveats"]:
            print(f"  caveat    {note}")
        # The rows that were passed over are printed alongside the one that was chosen. Withholding silently
        # is how asn1core looked like the next step for a week while no process could run its row.
        for row in step["skipped"][: args.limit]:
            print(f"  withheld  {row['project']}/{row['row']}: {row['why']}")
        print(f"  source    {step['source']}")
        print()
        print(step["command"])
    return 0 if "command" in step else 2


if __name__ == "__main__":
    raise SystemExit(main())
