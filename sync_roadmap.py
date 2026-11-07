"""Project each SDK's ROADMAP.md onto GitHub issues, and report when the two disagree.

Why this lives in the forge and not in a project. Filing a project's plan is not part of that project's
product, and a library repo that carries the tooling publishing its roadmap ends up holding credentials
it has no reason to hold. One tool over every project also means one place where the roadmap shapes are
understood, instead of eighteen copies fixing the same bug differently.

Why the markdown stays authoritative. Each project's `tests/test_roadmap_rows.py` reads `ROADMAP.md`, and
the portfolio rule is that a row moves to done only when a named test would fail if the behaviour
regressed. CI can enforce that by reading a file; it cannot enforce a board. So issues are derived from
the document, never the reverse. What issues add is the cloud surface: something to assign, comment on and
close, so an agent without a local clone can take work and know what "done" means.

Idempotence comes from a marker line hidden in each body, matched instead of titles, so a re-run or a
renamed title cannot fork an issue. Nothing is written without `--apply`; `--check` is for CI and exits
non-zero on drift in either direction while creating nothing.

Usage:
    py sync_roadmap.py                              # every qualifying project, plan only
    py sync_roadmap.py --project pkix --apply
    py sync_roadmap.py --check
    py sync_roadmap.py --apply --board          # and put the issues on a Project V2 board
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

# One project list, shared with verify.py in this directory, so the two cannot drift apart.
from verify import PROJECTS

MARKER = "<!-- roadmap:{} -->"
DEFAULT_BOARD = "Protocol SDK portfolio"
MILESTONE_RE = re.compile(r"\bM\d+\b")
STATUSES = {
    "[ok]": ("status:done", "close"),
    "[x]": ("status:done", "close"),
    "[part]": ("status:partial", "open"),
    "[ ]": ("status:unimplemented", "open"),
    "[no]": ("type:non-goal", "open"),
}
LABELS = {
    "status:unimplemented": ("#ededed", "row reads [ ]"),
    "status:partial": ("#fbca04", "row reads [part], with what is missing stated"),
    "status:done": (
        "#0e8a16",
        "row reads [ok] and a named test would fail if it regressed",
    ),
    "type:non-goal": ("#c5def5", "row reads [no]; deliberate non-goal"),
}
# Two shapes in the portfolio: pkix writes `anchors.load_anchor()`, certledger writes
# `cl.ambient-trust`. Both are ids, and the parens are stripped before matching.
ID_RE = re.compile(r"^([a-z0-9_]+)\.([a-z0-9_\-]+)$")


def gh() -> str:
    """Absolute path to the gh CLI, or a loud stop. A partial path is what bandit's S607 warns about."""
    found = shutil.which("gh")
    if not found:
        print("gh is not on PATH; install it or authenticate first", file=sys.stderr)
        raise SystemExit(2)
    return found


def run(argv: list[str], apply: bool) -> str:
    """Run a gh command, or print it. Every write to the cloud sits behind this one line."""
    printable = " ".join(a if len(a) < 58 else a[:55] + "..." for a in argv)
    if not apply:
        print(f"    would run: {printable}")
        return ""
    done = subprocess.run(
        [gh(), *argv[1:]], capture_output=True, text=True, check=False
    )  # noqa: S603
    if done.returncode != 0:
        message = f"    FAILED ({done.returncode}): {printable}\n    {done.stderr.strip()[:300]}"
        print(message, file=sys.stderr)
        raise SystemExit(1)
    return done.stdout.strip()


def milestone_of(cells: list[str]) -> str:
    """The milestone a row cites explicitly, or the empty string. Never inferred from names."""
    for cell in cells:
        found = MILESTONE_RE.search(cell)
        if found:
            return found.group()
    return ""


def milestones_of(text: str) -> dict[str, str]:
    """The milestone ladder, written `- M7 vendor program containers: ...`."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^- (M\d+)\s+(.*)$", line.strip())
        if match:
            found[match.group(1)] = match.group(2).split(":")[0].strip()
    return found


def rows_of(text: str) -> list[dict[str, str]]:
    """Obligation rows, on the same parsing contract as each project's tests/test_roadmap_rows.py.

    Columns past Status are optional: pkix ends at Where, certledger adds Evidence, which is the
    strongest thing in either document and so belongs in the issue body.
    """
    out: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 4:
            continue
        match = ID_RE.match(cells[0].strip("`").removesuffix("()"))
        if not match:
            continue
        out.append(
            {
                "name": f"{match.group(1)}.{match.group(2)}",
                "title": cells[0].strip("`"),
                "spec": cells[1],
                "status": cells[2].strip("`").lower(),
                "where": cells[3],
                "evidence": cells[4] if len(cells) > 4 else "",
                "milestone": milestone_of(cells),
            }
        )
    return out


def existing(repo: str, network: bool) -> dict[str, dict[str, object]]:
    """Issues keyed by their marker, read from the API rather than remembered locally.

    `network` gates the read, not a write: reads are side-effect free, but a plan run against a repo that
    does not exist yet should report what it would create instead of a failure.
    """
    if not network:
        return {}
    argv = [
        gh(),
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "all",
        "--limit",
        "500",
        "--json",
        "number,body,state",
    ]
    out = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    if out.returncode != 0:
        print(f"    could not read issues: {out.stderr.strip()[:160]}", file=sys.stderr)
        return {}
    by_key: dict[str, dict[str, object]] = {}
    for item in json.loads(out.stdout or "[]"):
        match = re.search(r"<!-- roadmap:(\S+) -->", str(item.get("body", "")))
        if match:
            by_key[match.group(1)] = item
    return by_key


def ensure_labels(repo: str, apply: bool) -> None:
    """Create the labels the matrix maps onto. `gh issue create --label` does not create them itself."""
    for name, (color, description) in LABELS.items():
        run(
            [
                "gh",
                "label",
                "create",
                name,
                "--repo",
                repo,
                "--color",
                color.lstrip("#"),
                "--description",
                description,
                "--force",
            ],
            apply,
        )


def ensure_milestones(repo: str, needed: set[str], apply: bool) -> None:
    """Create the milestone ladder. Milestones have no gh subcommand, so this goes through the api."""
    if not needed:
        return
    argv = [gh(), "api", f"repos/{repo}/milestones", "--paginate", "--jq", ".[].title"]
    out = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    have = set(out.stdout.split()) if out.returncode == 0 else set()
    for title in sorted(needed - have):
        run(
            [
                "gh",
                "api",
                f"repos/{repo}/milestones",
                "-f",
                f"title={title}",
                "-f",
                "state=open",
            ],
            apply,
        )


def body(row: dict[str, str], milestone: str) -> str:
    """The issue text: what the row claims, what proves it, and what closes it."""
    lines = [
        MARKER.format(row["name"]),
        "",
        f"Spec: {row['spec']}",
        f"Milestone: {milestone or '(none cited by the row)'}",
        f"Named test: {row['where']}",
        f"Row status in `ROADMAP.md`: `{row['status']}`",
    ]
    if row["evidence"]:
        lines.append(f"Evidence recorded in the row: {row['evidence']}")
    lines += [
        "",
        "## What closes this",
        "",
        "The `ROADMAP.md` row flips **in the same commit** as the code, and only when the named test",
        "would fail if the behaviour regressed. A row that reads done because the code looks right is the",
        "failure this portfolio exists to avoid, so this issue is not closed by a passing build.",
        "",
        "## Ground rules for whoever picks this up",
        "",
        "- **Normative text is never paraphrased from memory.** Fetch the spec section and cite it in the",
        "  commit with the document's title and revision. If the document is unreachable, say so in a",
        "  comment and leave the row as it is: a stub that raises beats a parser that agrees with itself.",
        "- Check the errata for anything cited here; a verified erratum changes the answer.",
        "- No silent empty values. Refuse loudly rather than return an empty collection.",
        "- Claim it by assigning yourself before starting, so two agents do not collide.",
        "- Gates before a PR: ruff check, ruff format --check, mypy, pytest with coverage, and the same",
        "  suite under `python -O`, because an obligation pinned only inside an `assert` is not pinned.",
        "",
    ]
    return "\n".join(lines)


def sync_one(name: str, repo: str, apply: bool, check: bool, root: pathlib.Path) -> int:
    """One project: file what is missing, close what is done, and report drift."""
    path = root / name / "ROADMAP.md"
    if not path.is_file():
        print(f"{name}: no ROADMAP.md, skipped")
        return 0
    text = path.read_text(encoding="utf-8")
    rows = rows_of(text)
    if not rows:
        print(f"{name}: prose plan, no obligation matrix; nothing filed")
        return 0
    read = apply or check
    have = existing(repo, read)
    names = {row["name"] for row in rows}
    open_rows = [
        row for row in rows if STATUSES.get(row["status"], ("", "open"))[1] == "open"
    ]
    drift = [
        f"{row['name']} is open with no issue"
        for row in open_rows
        if row["name"] not in have
    ] + [
        f"issue #{item['number']} marks {key}, which is no longer a row"
        for key, item in have.items()
        if key not in names
    ]
    # A plan run does not read the board, so it has no evidence for a drift count and prints none.
    print(
        f"{name}: {len(rows)} rows"
        + (f", {len(have)} marked issues, {len(drift)} drift" if read else "")
    )
    if check:
        for line in drift:
            print(f"    DRIFT: {line}", file=sys.stderr)
        return len(drift)

    ensure_labels(repo, apply)
    ensure_milestones(repo, set(milestones_of(text)), apply)
    for row in rows:
        label, action = STATUSES.get(row["status"], ("status:unimplemented", "open"))
        item = have.get(row["name"])
        if item is not None:
            if action == "close" and item["state"] == "OPEN":
                run(
                    ["gh", "issue", "close", str(item["number"]), "--repo", repo], apply
                )
            continue
        if action == "close":
            continue  # a done obligation needs no issue: the test is what carries that claim
        print(f"    {row['name']:<34} create ({label})")
        file = (
            pathlib.Path(tempfile.gettempdir())
            / f"{name}-roadmap-{row['name']}.issue.md"
        )
        file.write_text(body(row, row["milestone"]), encoding="utf-8")
        run(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                f"{row['title']} - {row['spec']}",
                "--body-file",
                str(file),
                "--label",
                label,
                *(["--milestone", row["milestone"]] if row["milestone"] else []),
            ],
            apply,
        )
        file.unlink(missing_ok=True)
    return 0


def graphql(query: str) -> str:
    """One GraphQL call through gh, so the token in the keyring is the only credential involved."""
    out = subprocess.run(  # noqa: S603
        [gh(), "api", "graphql", "-f", f"query={query}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        print(f"    board: {out.stderr.strip()[:200]}", file=sys.stderr)
        return ""
    return out.stdout


def board_urls(owner: str, number: int) -> set[str]:
    """Issue URLs already on the board, in one call."""
    query = (
        f'query {{ user(login: "{owner}") {{ projectV2(number: {number}) {{ items(first: 200) {{ nodes '
        f"{{ content {{ ... on Issue {{ url }} }} }} }} }} }} }}"
    )
    text = graphql(query)
    if not text:
        return set()
    nodes = json.loads(text)["data"]["user"]["projectV2"]["items"]["nodes"]
    return {str(n["content"]["url"]) for n in nodes if n.get("content")}


def sync_board(
    owner: str, title: str, want: list[tuple[str, str]], apply: bool
) -> None:
    """Create the board if absent, then add every issue not already on it.

    `want` is (url, node id) per issue. The additions go out as one aliased mutation rather than one
    request per issue: twenty-two sequential calls took long enough to trip a command timeout, and an
    interrupted loop is a half-populated board.
    """
    if not apply:
        print(
            f"board: with --apply, create {title!r} if absent and add up to {len(want)} issue(s)"
        )
        return
    listing = graphql(
        f'query {{ user(login: "{owner}") {{ projectV2s(first: 100) {{ nodes {{ id number title }} }} }} }}'
    )
    if not listing:
        return
    projects = json.loads(listing)["data"]["user"]["projectV2s"]["nodes"]
    project = next((p for p in projects if p["title"] == title), None)
    if project is None:
        out = run(
            [
                "gh",
                "project",
                "create",
                "--owner",
                owner,
                "--title",
                title,
                "--format",
                "json",
            ],
            apply,
        )
        try:
            project = json.loads(out)
        except json.JSONDecodeError:
            print(
                "board: created nothing usable; could not parse the response",
                file=sys.stderr,
            )
            return
        print(f"board: created {title} #{project['number']}")
    have = board_urls(owner, project["number"])
    missing = [(url, node) for url, node in want if url not in have]
    if not missing:
        print(
            f"board: {title} #{project['number']} already carries all {len(want)} issue(s)"
        )
        return
    parts = " ".join(
        f'add{index}: addProjectV2ItemById(input: {{ projectId: "{project["id"]}", contentId: "{node}" }}) '
        f"{{ item {{ id }} }}"
        for index, (_, node) in enumerate(missing)
    )
    graphql(f"mutation {{ {parts} }}")
    print(f"board: added {len(missing)} issue(s) to {title} #{project['number']}")


def issue_refs(repo: str) -> list[tuple[str, str]]:
    """(url, node id) for a repo's open issues. The node id is what a board item needs."""
    argv = [
        gh(),
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        "open",
        "--limit",
        "200",
        "--json",
        "url,id",
    ]
    out = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    if out.returncode != 0:
        return []
    return [(str(i["url"]), str(i["id"])) for i in json.loads(out.stdout or "[]")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", action="append", help="restrict to this project; repeatable"
    )
    parser.add_argument(
        "--org",
        default="b1tninja",
        help="mirrors forge.ORG, kept a flag so forge need not import",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create what is missing (default: plan only)",
    )
    parser.add_argument(
        "--check", action="store_true", help="CI mode: report drift, write nothing"
    )
    parser.add_argument(
        "--board",
        nargs="?",
        const=DEFAULT_BOARD,
        default=None,
        metavar="TITLE",
        help="add these projects' open issues to a Project V2 board of this title",
    )
    parser.add_argument(
        "--root",
        default=os.environ.get("FORGE_ROOT", "D:/code"),
        help="directory the project checkouts live in (FORGE_ROOT in CI)",
    )
    args = parser.parse_args()

    root = pathlib.Path(args.root)
    total = 0
    refs: list[tuple[str, str]] = []
    for name in args.project or PROJECTS:
        repo = f"{args.org}/{name}"
        total += sync_one(name, repo, args.apply, args.check, root)
        if args.board and not args.check:
            refs += issue_refs(repo)
    if args.board:
        sync_board(args.org, args.board, refs, args.apply)
    if args.check:
        print("in sync" if not total else f"{total} drift item(s)")
        return 1 if total else 0
    if not args.apply:
        print("dry run: nothing created. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
