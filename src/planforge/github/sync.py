"""Project each SDK's obligation matrix onto GitHub issues, and report when the two disagree.

Why this lives in the forge and not in a project. Filing a project's plan is not part of that project's
product, and a library repo that carries the tooling publishing its roadmap ends up holding credentials
it has no reason to hold. One tool over every project also means one place where the roadmap shapes are
understood, instead of eighteen copies fixing the same bug differently.

Why the markdown stays authoritative. Each project's `tests/test_roadmap_rows.py` reads its matrix, and
the portfolio rule is that a row moves to done only when a named test would fail if the behaviour
regressed. CI can enforce that by reading a file; it cannot enforce a board. So issues are derived from
the document, never the reverse. What issues add is the cloud surface: something to assign, comment on and
close, so an agent without a local clone can take work and know what "done" means.

The grammar (rows, statuses, ladders, evidence) is roadmap.py's; this module only maps it onto issues. A
project resolves to its directory (itulib lives in itulibrary), its matrix file (ROADMAP.md, PLAN.md or
docs/roadmap.md) and its GitHub repository (read from the checkout's own remote).

Safety, because this writes to many repositories at once:

- Idempotence comes from a marker line hidden in each body, matched instead of titles, so a re-run or a
  renamed title cannot fork an issue.
- Every read happens before the first write, and a read that failed or may be truncated stops that
  project: an empty answer read as "no issues yet" used to file a duplicate for every open row.
- Only the labels and milestones that rows will actually carry are created, and only when absent.
- Writes are paced under GitHub's secondary limits for content-creating requests (80 a minute, 500 an
  hour). When the hour's budget is spent the run stops and says where; re-running continues from there.
- Nothing is written without `--apply`; `--check` is for CI and exits non-zero on drift in either
  direction (an open row whose issue is closed, a done row whose issue is open, a milestone mismatch, a
  row with no issue, an issue with no row, a read that failed) while writing nothing.

The Project V2 board is board.py's, not this tool's.

Usage:
    py sync_roadmap.py                              # every synced project, plan only
    py sync_roadmap.py --project pkix --apply
    py sync_roadmap.py --check
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
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from planforge import registry as portfolio
from planforge.db import parser as roadmap
from planforge.github import rowdoc

# One project list, shared with verify.py in this directory, so the two cannot drift apart. planner.py
# imports it from here.
from planforge.data import PROJECTS

MARKER = "<!-- roadmap:{} -->"
MARKER_RE = re.compile(r"<!-- roadmap:(\S+) -->")
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
# A read that returns exactly this many items may have been cut off, and is refused rather than trusted.
ISSUE_LIMIT = 1000
LABEL_LIMIT = 1000
# GitHub's documented secondary limits for content-creating requests. The defaults stay below them, and a
# configuration above them is refused.
GITHUB_PER_MINUTE = 80
GITHUB_PER_HOUR = 500

# The grammar is roadmap.py's. These names stay importable here for board.py, planner.py, deps.py and
# portfolio.py, which were written against them.
rows_of = roadmap.rows_of


class ReadError(RuntimeError):
    """A read from GitHub failed, or came back in a shape that may be incomplete."""


class WriteError(RuntimeError):
    """A write to GitHub failed. The run stops at it."""


class BudgetExhausted(RuntimeError):
    """The hour's write budget is spent. The run stops instead of waiting an hour."""


# ---------------------------------------------------------------------------------------------- pacing


@dataclass
class Pacer:
    """Spaces writes so a run stays under GitHub's secondary limits.

    `delay` is the minimum gap between two writes. `per_minute` makes the run wait for a sliding minute to
    free a slot; `per_hour` makes it stop, because waiting most of an hour inside one run is worse than
    re-running later. The counts are this process's own: a run started within an hour of another one does
    not see the earlier writes, which is why the defaults leave a margin.
    """

    delay: float = 1.0
    per_minute: int = 60
    per_hour: int = 450
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    stamps: list[float] = field(default_factory=list)
    where: str = ""

    @property
    def count(self) -> int:
        return len(self.stamps)

    def wait(self) -> None:
        now = self.clock()
        if self.stamps and self.delay > 0:
            gap = self.delay - (now - self.stamps[-1])
            if gap > 0:
                self.sleep(gap)
                now = self.clock()
        hour = [t for t in self.stamps if now - t < 3600]
        if len(hour) >= self.per_hour:
            raise BudgetExhausted(
                f"{len(hour)} writes in the last hour reached --per-hour {self.per_hour}"
            )
        minute = [t for t in hour if now - t < 60]
        if len(minute) >= self.per_minute:
            self.sleep(60 - (now - minute[-self.per_minute]))
            now = self.clock()
        self.stamps.append(now)


PACER = Pacer()


# ---------------------------------------------------------------------------------------------- gh


def gh() -> str:
    """Absolute path to the gh CLI, or a loud stop. A partial path is what bandit's S607 warns about."""
    found = shutil.which("gh")
    if not found:
        print("gh is not on PATH; install it or authenticate first", file=sys.stderr)
        raise SystemExit(2)
    return found


def run(argv: list[str], apply: bool) -> str:
    """Run a gh write, or print it. Every write to the cloud sits behind this one function, paced."""
    printable = " ".join(a if len(a) < 58 else a[:55] + "..." for a in argv)
    if not apply:
        print(f"    would run: {printable}")
        return ""
    PACER.wait()
    done = subprocess.run(  # noqa: S603
        [gh(), *argv[1:]], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if done.returncode != 0:
        raise WriteError(
            f"({done.returncode}) {printable}: {done.stderr.strip()[:300]}"
        )
    return done.stdout.strip()


def read(argv: list[str], what: str) -> str:
    """Run a gh read. A failure raises: an empty answer must never stand in for "nothing there"."""
    done = subprocess.run(  # noqa: S603
        [gh(), *argv], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if done.returncode != 0:
        raise ReadError(f"could not read {what}: {done.stderr.strip()[:160]}")
    return done.stdout


def _json_list(text: str, what: str) -> list[dict[str, object]]:
    try:
        items = json.loads(text or "[]")
    except json.JSONDecodeError as error:
        raise ReadError(f"{what} did not parse as JSON: {error}") from error
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise ReadError(f"{what} is not a list of objects")
    return items


def existing(
    repo: str, network: bool = True, limit: int = ISSUE_LIMIT
) -> dict[str, dict[str, object]]:
    """Issues keyed by their marker, read from the API rather than remembered locally.

    Raises ReadError when the read fails or returns `limit` issues, since a list cut off at the limit is
    indistinguishable from a complete one and would file a duplicate of every issue past the cut. When two
    issues carry one marker the open one wins, so a stray duplicate cannot hide a live issue.
    """
    if not network:
        return {}
    out = read(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            str(limit),
            "--json",
            "number,title,body,state,milestone",
        ],
        f"issues of {repo}",
    )
    items = _json_list(out, f"the issues of {repo}")
    if len(items) >= limit:
        raise ReadError(
            f"{repo} returned {len(items)} issues, the read limit, so the list may be truncated"
        )
    by_key: dict[str, dict[str, object]] = {}
    for item in items:
        match = MARKER_RE.search(str(item.get("body", "")))
        if not match:
            continue
        key = match.group(1)
        if key not in by_key or state_of(item) == "OPEN":
            by_key[key] = item
    return by_key


def read_labels(repo: str, limit: int = LABEL_LIMIT) -> set[str]:
    out = read(
        ["label", "list", "--repo", repo, "--limit", str(limit), "--json", "name"],
        f"labels of {repo}",
    )
    items = _json_list(out, f"the labels of {repo}")
    if len(items) >= limit:
        raise ReadError(f"{repo} returned {len(items)} labels, the read limit")
    return {str(i.get("name", "")) for i in items}


def read_milestones(repo: str) -> list[dict[str, object]]:
    out = read(
        [
            "api",
            f"repos/{repo}/milestones?state=all",
            "--paginate",
            "--jq",
            ".[] | {number, title, description}",
        ],
        f"milestones of {repo}",
    )
    found: list[dict[str, object]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise ReadError(f"a milestone of {repo} did not parse: {error}") from error
        if not isinstance(item, dict):
            raise ReadError(f"a milestone of {repo} is not an object")
        found.append(item)
    return found


def state_of(item: dict[str, object]) -> str:
    return str(item.get("state", "")).upper()


def milestone_on(item: dict[str, object]) -> str:
    current = item.get("milestone")
    return str(current.get("title", "")) if isinstance(current, dict) else ""


# ---------------------------------------------------------------------------------------------- targets


@dataclass(frozen=True)
class Target:
    name: str
    directory: pathlib.Path
    matrix: pathlib.Path | None
    repo: str


def resolve(
    root: pathlib.Path,
    name: str,
    org: str,
    remote: Callable[[pathlib.Path, str], str] = portfolio.github_repo,
) -> Target:
    """Directory, matrix file and GitHub repository for a project name.

    The repository comes from the checkout's own remote, so itulib (directory itulibrary) files into
    b1tninja/itulib. A project with no remote falls back to `org/name`, which a plan run then reports as
    absent instead of guessing further.
    """
    directory = roadmap.project_dir(root, name)
    repo = remote(root, directory.name) if directory.is_dir() else ""
    return Target(
        name, directory, roadmap.matrix_path(root, name), repo or f"{org}/{name}"
    )


def default_projects() -> list[str]:
    """The shells verify.py gates plus the hand-owned matrices D19 syncs, each once."""
    return list(dict.fromkeys([*PROJECTS, *roadmap.HAND_OWNED]))


# ---------------------------------------------------------------------------------------------- writes


def ensure_labels(
    repo: str, wanted: Iterable[str], have: set[str], apply: bool
) -> None:
    """Create the labels rows will carry and the repo lacks. `gh issue create --label` will not."""
    for name in sorted(set(wanted) - have):
        color, description = LABELS[name]
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
            ],
            apply,
        )


def ensure_milestones(
    repo: str,
    ladder: dict[str, str],
    keys: Iterable[str],
    have: list[dict[str, object]],
    apply: bool,
) -> None:
    """Create or bring up to date the milestones rows cite. Milestones have no gh subcommand, so this is
    the api.

    A milestone is titled `M1 - what it delivers`, because a board shows the title and a bare `M1` tells a
    reader nothing. An existing milestone titled just `M1` is renamed rather than duplicated, and its
    description carries the ladder's full text. A key the ladder does not describe is not created: the
    tool never invents a rung.
    """
    for key in sorted(k for k in set(keys) if k in ladder):
        title = rowdoc.milestone_title(key, ladder)
        description = ladder[key]
        current = next(
            (
                m
                for m in have
                if str(m.get("title", "")) in (title, key)
                or str(m.get("title", "")).startswith(f"{key} ")
            ),
            None,
        )
        api = f"repos/{repo}/milestones"
        fields = ["-f", f"title={title}", "-f", f"description={description}"]
        if current is None:
            run(["gh", "api", api, "-f", "state=open", *fields], apply)
        elif (
            current.get("title") != title
            or str(current.get("description") or "") != description
        ):
            run(
                ["gh", "api", "-X", "PATCH", f"{api}/{current['number']}", *fields],
                apply,
            )


def placement(row: dict[str, str], ladder: dict[str, str]) -> str:
    """The milestone title a row's issue should carry: "" when the row cites none the ladder describes."""
    key = row.get("milestone", "")
    return rowdoc.milestone_title(key, ladder) if key in ladder else ""


def roadmap_url(repo: str) -> str:
    return f"https://github.com/{repo}/blob/HEAD/ROADMAP.md"


def body(row: dict[str, str], milestone: str, detail: str = "") -> str:
    """The issue text: which repo and why, what to build, what proves it, and what closes it."""
    lines = [MARKER.format(row["name"]), ""]
    if detail:
        lines += [detail, ""]
    else:
        lines += [
            f"Spec: {row['spec']}",
            f"Milestone: {milestone or '(none cited by the row)'}",
            f"Named test: {row['where']}",
            f"Row status in `ROADMAP.md`: `{row['status']}`",
        ]
        if row["evidence"]:
            lines.append(f"Evidence recorded in the row: {row['evidence']}")
        lines.append("")
    lines += [
        "## What closes this",
        "",
        "The `ROADMAP.md` row flips **in the same commit** as the code, and only when the named test",
        "would fail if the behaviour regressed. A row that reads done because the code looks right is the",
        "failure this portfolio exists to avoid, so this issue is not closed by a passing build.",
        "",
        "<details><summary>Ground rules for whoever picks this up</summary>",
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
        "</details>",
        "",
    ]
    return "\n".join(lines)


def detail_of(
    name: str,
    repo: str,
    row: dict[str, str],
    rows: list[dict[str, str]],
    text: str,
    root: pathlib.Path,
    ladder: dict[str, str],
) -> tuple[str, str]:
    """(title summary, body section) built from the project's own files. Nothing here is invented."""
    stub = rowdoc.stub_of(root, name, row["name"])
    blurb = rowdoc.project_blurb(root, name)
    milestone = row["milestone"]
    placed = rowdoc.milestone_title(milestone, ladder)
    lines = [
        f"**Repository:** [`{repo}`](https://github.com/{repo})  |  "
        f"**Milestone:** {placed or 'not placed on the ladder in ROADMAP.md'}",
        "",
        "## What this is",
        "",
    ]
    if blurb:
        lines += [f"`{name}`: {blurb}", ""]
    if stub is not None:
        lines += [f"Module `{stub.path}`: {stub.module_summary}", ""]
    lines += ["## What is needed", ""]
    if stub is not None:
        state = (
            "still a stub that raises `NotImplementedError`"
            if stub.still_stub
            else "no longer a bare stub, but its row is not `[ok]`"
        )
        lines += [
            f"Implement `{stub.signature}` in `{stub.path}`, currently {state}.",
            "",
        ]
    lines += [f"Behaviour is defined by: **{row['spec']}**."]
    lines += [f"- {e}" for e in roadmap.errata_lines(text, row["spec"])]
    lines += [""]
    for note in rowdoc.notes_for(text, row["title"]):
        lines += [f"> {note}", ""]
    ok, why = roadmap.evidence(root, name, row["where"], row.get("evidence", ""))
    proof = (
        why
        if ok
        else f"**does not exist yet** ({why}). Writing it is part of this work."
    )
    lines += [
        "## Done when",
        "",
        f"- Named test: `{row['where'].strip('`')}` - {proof}",
        f"- The `{row['title']}` row in [ROADMAP.md]({roadmap_url(repo)}) flips from "
        f"`{row['status']}` to `[ok]`.",
    ]
    mates = rowdoc.siblings(rows, milestone, row["name"])
    if milestone and mates:
        listed = ", ".join(f"`{m}`" for m in mates)
        lines.append(
            f"- Milestone {milestone} is accepted when these rows are also done: {listed}."
        )
    lines += [""]
    dependencies = rowdoc.dependency_lines(root, name)
    if dependencies:
        lines += ["## Depends on (state read from the workspace)", ""]
        lines += [f"- {d}" for d in dependencies]
        lines += [
            "",
            "A dependency existing does not mean it provides what this row needs; check the capability.",
            "",
        ]
    return rowdoc.short_summary(stub), "\n".join(lines)


def issue_title(name: str, row: dict[str, str], summary: str) -> str:
    """`[pkix] anchors.load_anchor(): trust anchor selection`. The repository is in the title because a
    board row drawn from many repositories is otherwise anonymous."""
    return f"[{name}] {row['title']}: {summary or row['spec']}"


def same_text(a: str, b: str) -> bool:
    return a.replace("\r\n", "\n").strip() == b.replace("\r\n", "\n").strip()


def repo_exists(repo: str) -> bool:
    """Is there a GitHub repository to file into? Most projects here are local-only and have none."""
    done = subprocess.run(  # noqa: S603
        [gh(), "repo", "view", repo, "--json", "name"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return done.returncode == 0


# ---------------------------------------------------------------------------------------------- drift


def action_of(row: dict[str, str]) -> tuple[str, str]:
    return STATUSES.get(row["status"], ("status:unimplemented", "open"))


def drift_of(
    rows: list[dict[str, str]],
    have: dict[str, dict[str, object]],
    ladder: dict[str, str],
) -> list[str]:
    """Every disagreement between the matrix and the issues, in either direction."""
    found: list[str] = []
    names = {row["name"] for row in rows}
    for row in rows:
        _, action = action_of(row)
        item = have.get(row["name"])
        if item is None:
            if action == "open":
                found.append(f"{row['name']} is open with no issue")
            continue
        number, state = item.get("number"), state_of(item)
        if action == "open" and state == "CLOSED":
            found.append(
                f"{row['name']} is {row['status']} but issue #{number} is closed"
            )
        elif action == "close" and state == "OPEN":
            found.append(
                f"{row['name']} is {row['status']} but issue #{number} is still open"
            )
        elif action == "open" and state == "OPEN":
            wanted, current = placement(row, ladder), milestone_on(item)
            if wanted != current:
                found.append(
                    f"issue #{number} for {row['name']} is on milestone "
                    f"{current or '(none)'!r}, the row places it on {wanted or '(none)'!r}"
                )
    found += [
        f"issue #{item.get('number')} marks {key}, which is no longer a row"
        for key, item in have.items()
        if key not in names
    ]
    return found


# ---------------------------------------------------------------------------------------------- sync


def sync_one(
    name: str,
    repo: str,
    apply: bool,
    check: bool,
    root: pathlib.Path,
    matrix: pathlib.Path | None = None,
) -> int:
    """One project: file what is missing, bring open issues up to date, close what is done, report drift.

    Returns the number of problems: drift items under --check, and 1 for a read that failed otherwise.
    """
    path = matrix or roadmap.matrix_path(root, name)
    if path is None or not path.is_file():
        print(
            f"{name}: no obligation matrix ({', '.join(roadmap.MATRIX_FILES)}), skipped"
        )
        return 0
    text = path.read_text(encoding="utf-8")
    parsed = roadmap.parse(text)
    rows = [row for row in parsed.rows if row["name"]]
    broken = [
        f"{path.name}:{p.line} looks like a row but does not parse: {p.reason}"
        for p in parsed.problems
    ]
    for line in broken:
        print(f"    WARNING: {line}", file=sys.stderr)
    if not rows:
        print(f"{name}: prose plan, no obligation matrix; nothing filed")
        return len(broken) if check else 0
    # Plan and apply skip a project with no repository, because the first write against it would end the
    # whole run. --check does not skip: a project whose issues cannot be read is not "in sync", and a gate
    # that quietly passes on an unreachable repository reports green for the wrong reason.
    if not check and not repo_exists(repo):
        print(f"{name}: no GitHub repository {repo}; skipped (create it, then re-run)")
        return 0
    ladder = (
        roadmap.ladder_for(root, name)
        if name in roadmap.LADDERS
        else roadmap.ladder_of(text, "M")
    )
    open_rows = [row for row in rows if action_of(row)[1] == "open"]
    keys = {row["milestone"] for row in open_rows if row["milestone"] in ladder}
    # Every read happens before the first write. Reads are side-effect free, so a plan run reads too:
    # without that it reports every issue as a create, which is a preview that lies.
    try:
        have = existing(repo, True)
        labels = read_labels(repo) if not check else set()
        milestones = read_milestones(repo) if not check and keys else []
    except ReadError as error:
        if check:
            print(f"{name}: 1 drift", file=sys.stdout)
            print(f"    DRIFT: {error}", file=sys.stderr)
            return 1 + len(broken)
        print(f"{name}: {error}; nothing written for this project", file=sys.stderr)
        return 1
    drift = drift_of(rows, have, ladder)
    print(f"{name}: {len(rows)} rows, {len(have)} marked issues, {len(drift)} drift")
    if check:
        for line in drift + broken:
            print(f"    DRIFT: {line}", file=sys.stderr)
        return len(drift) + len(broken)

    creating = [row for row in open_rows if row["name"] not in have]
    PACER.where = f"{name} (labels)"
    ensure_labels(repo, {action_of(row)[0] for row in creating}, labels, apply)
    PACER.where = f"{name} (milestones)"
    ensure_milestones(repo, ladder, keys, milestones, apply)
    for row in rows:
        PACER.where = f"{name} {row['name']}"
        label, action = action_of(row)
        item = have.get(row["name"])
        if item is not None:
            state = state_of(item)
            if action == "close" and state == "OPEN":
                run(
                    ["gh", "issue", "close", str(item["number"]), "--repo", repo], apply
                )
            elif action == "open" and state == "OPEN":
                update_issue(name, repo, row, rows, text, root, ladder, item, apply)
            elif action == "open":
                print(
                    f"    {row['name']:<34} issue #{item['number']} is closed but the row is "
                    f"{row['status']}: reopen it or flip the row (not done automatically)"
                )
            continue
        if action == "close":
            continue  # a done obligation needs no issue: the test is what carries that claim
        print(f"    {row['name']:<34} create ({label})")
        summary, detail = detail_of(name, repo, row, rows, text, root, ladder)
        milestone = placement(row, ladder)
        file = (
            pathlib.Path(tempfile.gettempdir())
            / f"{name}-roadmap-{row['name']}.issue.md"
        )
        file.write_text(
            body(row, row["milestone"], detail), encoding="utf-8", newline="\n"
        )
        try:
            run(
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    repo,
                    "--title",
                    issue_title(name, row, summary),
                    "--body-file",
                    str(file),
                    "--label",
                    label,
                    *(["--milestone", milestone] if milestone else []),
                ],
                apply,
            )
        finally:
            file.unlink(missing_ok=True)
    return 0


def update_issue(
    name: str,
    repo: str,
    row: dict[str, str],
    rows: list[dict[str, str]],
    text: str,
    root: pathlib.Path,
    ladder: dict[str, str],
    item: dict[str, object],
    apply: bool,
) -> None:
    """Edit an open issue whose title, body or milestone no longer matches what the row would produce."""
    summary, detail = detail_of(name, repo, row, rows, text, root, ladder)
    title = issue_title(name, row, summary)
    wanted = body(row, row["milestone"], detail)
    milestone = placement(row, ladder)
    current_title = milestone_on(item)
    changes = []
    if str(item.get("title", "")) != title:
        changes.append("title")
    if not same_text(str(item.get("body", "")), wanted):
        changes.append("body")
    if milestone != current_title:
        changes.append("milestone")
    if not changes:
        return
    print(f"    {row['name']:<34} update ({', '.join(changes)})")
    file = (
        pathlib.Path(tempfile.gettempdir()) / f"{name}-roadmap-{row['name']}.issue.md"
    )
    file.write_text(wanted, encoding="utf-8", newline="\n")
    argv = ["gh", "issue", "edit", str(item["number"]), "--repo", repo]
    argv += ["--title", title, "--body-file", str(file)]
    if milestone:
        argv += ["--milestone", milestone]
    elif current_title:
        argv += ["--remove-milestone"]
    try:
        run(argv, apply)
    finally:
        file.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    global PACER
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--project", action="append", help="restrict to this project; repeatable"
    )
    parser.add_argument(
        "--org",
        default=os.environ.get("FORGE_ORG") or "b1tninja",
        help="owner for a project whose checkout has no remote (FORGE_ORG)",
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
        "--root",
        default=os.environ.get("FORGE_ROOT", "D:/code"),
        help="directory the project checkouts live in (FORGE_ROOT in CI)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="minimum seconds between two writes (default 1.0)",
    )
    parser.add_argument(
        "--per-minute",
        type=int,
        default=60,
        help=f"writes allowed in any minute, at most {GITHUB_PER_MINUTE} (default 60)",
    )
    parser.add_argument(
        "--per-hour",
        type=int,
        default=450,
        help=f"writes before the run stops early, at most {GITHUB_PER_HOUR} (default 450)",
    )
    # Removed: board.py owns the Project V2 board. Kept as a flag only to say so instead of "unrecognized".
    parser.add_argument("--board", nargs="?", const="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.board is not None:
        parser.error(
            "--board was removed: board.py owns the board (board.py --number 4)"
        )
    if args.apply and args.check:
        parser.error("--apply and --check are exclusive: --check writes nothing")
    if not 0 < args.per_minute <= GITHUB_PER_MINUTE:
        parser.error(f"--per-minute must be between 1 and {GITHUB_PER_MINUTE}")
    if not 0 < args.per_hour <= GITHUB_PER_HOUR:
        parser.error(f"--per-hour must be between 1 and {GITHUB_PER_HOUR}")
    if args.delay < 0:
        parser.error("--delay must not be negative")
    PACER = Pacer(args.delay, args.per_minute, args.per_hour)

    root = pathlib.Path(args.root)
    total = 0
    for name in args.project or default_projects():
        target = resolve(root, name, args.org)
        try:
            total += sync_one(
                target.name, target.repo, args.apply, args.check, root, target.matrix
            )
        except BudgetExhausted as error:
            print(
                f"stopped early at {PACER.where} after {PACER.count} write(s): {error}. Nothing "
                "after it was written; re-run later to continue (issues are matched by marker, so "
                "nothing is filed twice).",
                file=sys.stderr,
            )
            return 3
        except WriteError as error:
            print(
                f"stopped at {PACER.where} after {PACER.count} write(s): a write failed {error}",
                file=sys.stderr,
            )
            return 1
    if args.check:
        print("in sync" if not total else f"{total} drift item(s)")
        return 1 if total else 0
    if not args.apply:
        print("dry run: nothing created. Re-run with --apply.")
    if total:
        print(f"{total} project(s) could not be read; nothing was written to them")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
