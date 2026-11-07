"""The forge's planning surface: read the portfolio, claim work, and update a row - with evidence.

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
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import rowdoc
from sync_roadmap import MARKER, PROJECTS, STATUSES, gh, rows_of

# Mirrors sync_roadmap's --root/FORGE_ROOT. Importing ROOT from verify.py instead would have hardcoded
# this machine's layout into every read and write; the planner's own tests discovered that by passing
# here and failing in CI, where D:/code does not exist.
ROOT = pathlib.Path(os.environ.get("FORGE_ROOT") or "D:/code")

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
    return ROOT / project / "ROADMAP.md"


def portfolio_status(with_issues: bool = True) -> list[dict[str, object]]:
    """Every project with a matrix: rows by status, and the derived issue count when reachable."""
    out: list[dict[str, object]] = []
    for name in PROJECTS:
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
                    f"{ORG}/{name}",
                    "--state",
                    "open",
                    "--json",
                    "number",
                ]
            )
            entry["open_issues"] = len(listed) if isinstance(listed, list) else listed
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
    return [
        {
            "row": row["name"],
            "status": row["status"],
            "spec": row["spec"],
            "named_test": row["where"],
            "milestone": row["milestone"] or "",
            "evidence": row["evidence"],
        }
        for row in rows[:limit]
    ]


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


def test_evidence(project: str, where: str) -> tuple[bool, str]:
    """Does the row's named test actually exist and define the named symbol?

    One implementation, shared with the issue text (rowdoc.py): two copies of this check would drift, and
    this is the one the done-row guard rests on. It is a floor, not the rule: the portfolio's rule is that
    a row flips only when the test would fail if the behaviour regressed, and only CI running that test
    can show that.
    """
    return rowdoc.test_evidence(ROOT, project, where)


def rewrite_row(text: str, project: str, name: str, new_status: str) -> tuple[str, str]:
    """Return (new text, refusal). Empty refusal means the row was changed."""
    if new_status not in STATUSES:
        return (
            text,
            f"{new_status!r} is not a status this portfolio recognises: {sorted(STATUSES)}",
        )
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 4 or cells[0].strip("`").removesuffix("()") != name:
            continue
        current = cells[2].strip("`").lower()
        if current == "[no]":
            return (
                text,
                f"{name} is a declared non-goal; that is a decision, not a status to set",
            )
        if new_status in ("[ok]", "[x]"):
            ok, why = test_evidence(project, cells[3])
            if not ok:
                return text, f"refusing to mark {name} done: {why}"
        cells[2] = f"`{new_status}`"
        lines[index] = "| " + " | ".join(cells) + " |\n"
        return "".join(lines), ""
    return text, f"no row named {name} in {project}/ROADMAP.md"


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
    return f"{project}/ROADMAP.md: {name} -> {new_status}. Commit it with the code and the named test."


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
    repo = f"{ORG}/{project}"
    listed = _gh_json(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "300",
            "--json",
            "number,title,url,body,assignees",
        ]
    )
    if not isinstance(listed, list):
        return f"could not read issues: {listed}"
    target = next(
        (i for i in listed if MARKER.format(name) in str(i.get("body", ""))), None
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


INSTRUCTIONS = (
    "Portfolio planning for the protocol SDK workspace. Read tools return the file or command an answer "
    "came from. ROADMAP.md is authoritative and issues are derived from it, so a row is the plan and the "
    "named test is the proof. Never mark a row done because the code looks right: this tool refuses when "
    "the named test does not exist, and CI is still the only thing that can show the test would fail if "
    "the behaviour regressed. Normative text is never paraphrased from memory; if a specification is "
    "unreachable, say so on the issue and leave the row as it is."
)


def build():
    """The MCP server. Imported here, not at module top, so the library works without mcp installed.

    The name matters: mcp 2.x renamed FastMCP to MCPServer, and every example on the internet still says
    `from mcp.server.fastmcp import FastMCP`. That import fails, and a client reports only that the
    server did not start.
    """
    from mcp.server.mcpserver import MCPServer

    app = MCPServer(name="sdk-forge", instructions=INSTRUCTIONS)

    @app.tool(
        description="Rows by state for every project with a matrix, plus open issue counts."
    )
    def forge_status(with_issues: bool = True) -> str:
        return json.dumps(portfolio_status(with_issues), indent=2)

    @app.tool(
        description="Unmet obligations in one project, each with its spec, named test and milestone."
    )
    def forge_next_actions(project: str, limit: int = 10) -> str:
        return json.dumps(next_actions(project, limit), indent=2)

    @app.tool(
        description="The prose around a row, where blocked notes and dead ends are recorded."
    )
    def forge_context(project: str, row: str) -> str:
        return context_for(project, row)

    @app.tool(
        description="Assign the row's issue to a login and confirm by re-reading. Refuses if anyone else holds it."
    )
    def forge_claim(project: str, row: str, who: str) -> str:
        return claim(project, row, who)

    @app.tool(
        description=(
            "Change a row's status in ROADMAP.md. Refuses a done status whose named test does not exist, "
            "and refuses to change a declared non-goal. write=false previews."
        )
    )
    def forge_propose_status(
        project: str, row: str, status: str, write: bool = False
    ) -> str:
        return propose_status(project, row, status, write)

    return app


def main() -> None:
    import anyio

    anyio.run(build().run_stdio_async)


if __name__ == "__main__":
    print(
        "sdk-forge planner: speak MCP on stdio; run it from a client, not a terminal",
        file=sys.stderr,
    )
    main()
